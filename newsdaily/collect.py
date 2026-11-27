"""RSS/Atom、公开页面及 JSON-LD 解析。不绕过登录或付费墙。"""
import concurrent.futures
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
from .publishers import publisher_metadata, article_url_pattern

UTC = timezone.utc
AGENT = 'LinfyeDaily/1.0'


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))).strip()


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        return ''
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_')
             and k.lower() not in ('fbclid', 'gclid', 'spm')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', urlencode(query), ''))


def parse_date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try:
            result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    # 不把缺失时区的日期擅自当作 UTC。
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def recent(article, now, hours):
    date = parse_date(article.get('published_at'))
    return date is not None and now - timedelta(hours=hours) <= date <= now + timedelta(minutes=5)


def item(source, title, url, description, published, body=''):
    url = canonical(url)
    title, description, body = clean(title), clean(description), clean(body)
    if not url or not title:
        return None
    # 源语言决定分组；明显错语种的入口不混入配额。
    cjk = len(re.findall(r'[\u3400-\u9fff]', title + description[:120]))
    if source['language'] == 'zh' and cjk == 0:
        return None
    if source['language'] == 'en' and cjk > len(title + description[:120]) * .35:
        return None
    return dict(id=hashlib.sha256(url.encode()).hexdigest()[:20], title=title, url=url,
                source_id=source['id'], source=source['name'], language=source['language'],
                source_region=source.get('region', ''),
                kind=source.get('kind', 'news'), published_at=published or '',
                excerpt=description[:2000], body=body[:10000],
                evidence='public_article' if body else 'feed_or_metadata')


def parse_feed(raw, source, base):
    root = ET.fromstring(raw)
    result = []
    for node in root.iter():
        if node.tag.split('}')[-1] not in ('item', 'entry'):
            continue
        fields = {}
        for child in node:
            tag = child.tag.split('}')[-1]
            value = ''.join(child.itertext()).strip()
            if tag == 'link':
                if child.attrib.get('rel', 'alternate') == 'alternate':
                    fields['link'] = child.attrib.get('href') or value
            else:
                fields[tag] = value
        date = parse_date(fields.get('pubDate') or fields.get('published') or fields.get('date'))
        # Atom updated 只在没有 published 时作为时间使用，标注在输入数据中。
        date_basis = 'published'
        if date is None and not fields.get('published') and fields.get('updated'):
            date = parse_date(fields['updated'])
            date_basis = 'updated'
        link = fields.get('link') or fields.get('guid', '')
        if not link:
            continue
        a = item(source, fields.get('title'), urljoin(base, link),
                 fields.get('description') or fields.get('summary') or fields.get('content'),
                 date.isoformat() if date else '')
        if a:
            a['date_basis'] = date_basis
            result.append(a)
    return result


class Page(HTMLParser):
    def __init__(self, raw, base):
        super().__init__(convert_charrefs=True)
        self.base, self.meta, self.feeds, self.links, self.ld = base, {}, [], [], []
        self.script, self.chunks = False, []
        self.anchor, self.anchor_text = None, []
        self.title, self.in_title = '', False
        self.feed(raw)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            self.meta[a.get('property') or a.get('name') or ''] = a.get('content', '')
        if tag == 'link' and 'alternate' in a.get('rel', '') and any(
                x in a.get('type', '') for x in ('rss', 'atom')):
            self.feeds.append(urljoin(self.base, a.get('href', '')))
        if tag == 'script' and a.get('type', '').split(';')[0] == 'application/ld+json':
            self.script, self.chunks = True, []
        if tag == 'a':
            self.anchor, self.anchor_text = a.get('href'), []
        if tag == 'title':
            self.in_title = True

    def handle_data(self, data):
        if self.script:
            self.chunks.append(data)
        if self.anchor:
            self.anchor_text.append(data)
        if self.in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.script:
            try:
                self.ld.append(json.loads(''.join(self.chunks)))
            except ValueError:
                pass
            self.script = False
        if tag == 'a' and self.anchor:
            self.links.append((urljoin(self.base, self.anchor), clean(' '.join(self.anchor_text))))
            self.anchor = None
        if tag == 'title':
            self.in_title = False


def ld_articles(value):
    if isinstance(value, list):
        for v in value:
            yield from ld_articles(v)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        types = [types] if isinstance(types, str) else types
        if any(t in ('NewsArticle', 'Article', 'ReportageNewsArticle', 'BlogPosting',
                     'AnalysisNewsArticle', 'OpinionNewsArticle') for t in types):
            yield value
        for v in value.values():
            if isinstance(v, (dict, list)):
                yield from ld_articles(v)


def parse_article(raw, source, url):
    page = Page(raw.decode('utf-8', errors='replace'), url)
    for article in ld_articles(page.ld):
        # 列表页内嵌的其他文章不是当前页面正文。
        candidate = article.get('url') or article.get('mainEntityOfPage')
        if isinstance(candidate, dict):
            candidate = candidate.get('@id')
        if isinstance(candidate, str) and canonical(urljoin(url, candidate)) != canonical(url):
            continue
        a = item(source, article.get('headline') or article.get('name'), url,
                 article.get('description') or page.meta.get('description'),
                 article.get('datePublished'), article.get('articleBody', ''))
        if a:
            a['date_basis'] = 'published'
            return a
    date = page.meta.get('article:published_time') or page.meta.get('datePublished')
    if date:
        a = item(source, page.meta.get('og:title') or page.title, url,
                 page.meta.get('og:description') or page.meta.get('description'), date)
        if a:
            a['date_basis'] = 'published'
        return a
    metadata = publisher_metadata(raw.decode('utf-8', errors='replace'), source, url)
    if metadata:
        a = item(source, metadata['title'], url,
                 page.meta.get('og:description') or page.meta.get('description'), metadata['published_at'])
        if a:
            a.update({k: metadata[k] for k in ('date_basis', 'date_timezone_assumed')})
        return a
    return None


class Fetcher:
    def __init__(self, timeout=20, budget=90):
        self.timeout, self.robots = timeout, {}
        self.deadline = time.monotonic() + budget

    def raw(self, url):
        if urlsplit(url).scheme not in ('http', 'https'):
            raise ValueError('只允许 HTTP(S) 地址')
        for attempt in range(2):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('单一来源抓取时间预算已用完')
            try:
                with urlopen(Request(url, headers={'User-Agent': AGENT,
                              'Accept': 'application/rss+xml, application/atom+xml, text/html, */*'}),
                             timeout=min(self.timeout, remaining)) as response:
                    chunks, total = [], 0
                    while True:
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError('单一来源抓取时间预算已用完')
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 4_000_000:
                            raise ValueError('页面超过 4MB 限制')
                        chunks.append(chunk)
                    return b''.join(chunks), response.geturl()
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt:
                    raise
            except (OSError, TimeoutError):
                if attempt:
                    raise
            time.sleep(1)

    def get(self, url):
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            robot = urllib.robotparser.RobotFileParser()
            try:
                raw, _ = self.raw(origin + '/robots.txt')
                robot.parse(raw.decode('utf-8', errors='replace').splitlines())
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    robot.parse([])
                else:
                    raise ValueError(f'robots.txt 无法确认（HTTP {e.code}）') from None
            robot_delay = robot.crawl_delay(AGENT) or 0
            self.robots[origin] = (robot, max(0.25, robot_delay), 0)
        robot, delay, last = self.robots[origin]
        if not robot.can_fetch(AGENT, url):
            raise ValueError('robots.txt 禁止抓取')
        wait = max(0, delay - (time.monotonic() - last))
        if time.monotonic() + wait >= self.deadline:
            raise TimeoutError('单一来源抓取时间预算已用完')
        time.sleep(wait)
        try:
            return self.raw(url)
        finally:
            self.robots[origin] = (robot, delay, time.monotonic())


def collect_source(source, cfg, now):
    article_limit = cfg.get('max_articles_per_source_by_language', {}).get(source['language'], cfg['max_articles_per_source'])
    scan_limit = max(article_limit * 3, 45)
    status = {'source_id': source['id'], 'source': source['name'], 'status': 'ok',
              'count': 0, 'warnings': []}
    if not source.get('enabled', True) or not (source.get('url') or source.get('feeds')):
        status.update(status='needs_config', warnings=[source.get('note', '需要文章列表/RSS/账号链接')])
        return [], status
    fetcher, articles = Fetcher(cfg['request_timeout_seconds'], cfg['source_timeout_seconds']), []
    feeds, page = list(source.get('feeds', [])), None
    if not feeds:
        try:
            raw, final_url = fetcher.get(source['url'])
            page = Page(raw.decode('utf-8', errors='replace'), final_url)
            feeds.extend(page.feeds[:3])
        except Exception as e:
            status['warnings'].append(type(e).__name__ + ': ' + str(e)[:180])
    for feed in feeds[:3]:
        try:
            raw, url = fetcher.get(feed)
            articles.extend(parse_feed(raw, source, url))
        except Exception as e:
            status['warnings'].append('RSS: ' + type(e).__name__ + ': ' + str(e)[:160])
    if not articles and source.get('url'):
        try:
            if page is None:
                raw, url = fetcher.get(source['url'])
                page = Page(raw.decode('utf-8', errors='replace'), url)
            origin = urlsplit(source['url']).hostname.removeprefix('www.')
            seen, candidates = set(), []
            for url, title in page.links:
                url = canonical(url)
                host = (urlsplit(url).hostname or '').removeprefix('www.')
                if (url and (host == origin or host.endswith('.' + origin)) and
                        len(title) >= 10 and url not in seen and urlsplit(url).path not in ('', '/')):
                    seen.add(url)
                    candidates.append(url)
            pattern = article_url_pattern(source)
            if pattern:
                candidates = [u for u in candidates if re.search(pattern, urlsplit(u).path)]
            for url in candidates[:scan_limit]:
                if time.monotonic() >= fetcher.deadline:
                    status['warnings'].append('来源抓取时间预算已用完')
                    break
                if len({a['url'] for a in articles if recent(a, now, cfg['lookback_hours'])}) >= article_limit:
                    break
                try:
                    raw, final_url = fetcher.get(url)
                    a = parse_article(raw, source, final_url)
                    if a:
                        articles.append(a)
                except Exception as e:
                    status['warnings'].append('文章: ' + type(e).__name__ + ': ' + str(e)[:100])
        except Exception as e:
            status['warnings'].append('公开页面: ' + str(e)[:160])
    unique = {a['url']: a for a in articles if recent(a, now, cfg['lookback_hours'])}
    articles = sorted(unique.values(), key=lambda a: a['published_at'], reverse=True)[
        :article_limit]
    status['count'] = len(articles)
    status['warnings'] = status['warnings'][:8]
    if not articles:
        status['status'] = 'unavailable' if status['warnings'] else 'no_recent_articles'
    elif status['warnings']:
        status['status'] = 'partial'
    return articles, status


def collect(sources, cfg, now):
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
        futures = {pool.submit(collect_source, s, cfg, now): s for s in sources}
        for f in concurrent.futures.as_completed(futures):
            s = futures[f]
            results[s['id']] = f.result()
            print(f"[{len(results)}/{len(sources)}] {s['name']}: {results[s['id']][1]['count']}", flush=True)
    articles, statuses = [], []
    for s in sources:
        aa, status = results[s['id']]
        articles.extend(aa)
        statuses.append(status)
    # 轮询各源选候选，避免名单前面的媒体耗尽 API 预算。
    selected, seen = [], set()
    for lang in ('zh', 'en'):
        limit = cfg.get('max_candidates_by_language', {}).get(lang, cfg['max_candidates_per_language'])
        buckets = [[a for a in articles if a['source_id'] == s['id'] and a['language'] == lang]
                   for s in sources]
        n = 0
        while any(buckets) and n < limit:
            for bucket in buckets:
                if bucket and n < limit:
                    a = bucket.pop(0)
                    key = canonical(a['url'])
                    title_key = (lang, re.sub(r'\W', '', a['title']).lower())
                    if key not in seen and title_key not in seen:
                        selected.append(a)
                        seen.update((key, title_key))
                        n += 1
    return selected, statuses
