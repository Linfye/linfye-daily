"""已核对公开页面结构的中文媒体发布时间适配；不执行页面 JavaScript。"""
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


# 只接受媒体明确的发布时间字段，不用正文或推荐栏中的任意日期凑数。
RULES = {
    'yicai': ('yicai.com', r"\[\s*['\"]actime['\"]\s*,\s*['\"]([^'\"]+)", r'/news/\d+\.html'),
    'infzm': ('infzm.com', r'<span\b(?=[^>]*\bnfzm-content__publish\b)[^>]*\bdata-time=[\"\x27]([^\"\x27]+)', r'/contents/\d+'),
    'eeo': ('eeo.com.cn', r"\bvar\s+publishedTime\s*=\s*['\"]([^'\"]+)", r'/20\d{2}/\d{4}/\d+\.shtml'),
    'cyol': ('cyol.com', r'<!--\s*enpproperty\b(?:(?!-->).)*?<date>([^<]+)</date>', r'/articles/20\d{2}-\d{2}/\d{2}/content_'),
    'xinhua': ('news.cn', r'<div\b[^>]*class=[\"\x27]info[\"\x27][^>]*>\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', r'/20\d{6}/[^/]+/c\.html'),
}


def article_url_pattern(source):
    rule = RULES.get(source['id'])
    return source.get('article_url_pattern') or (rule[2] if rule else '')


def publisher_metadata(raw, source, url):
    rule = RULES.get(source['id'])
    if not rule:
        return None
    domain, pattern, article_pattern = rule
    host = (urlsplit(url).hostname or '').lower()
    if not (host == domain or host.endswith('.' + domain)) or not re.search(article_pattern, urlsplit(url).path):
        return None
    match = re.search(pattern, raw, re.I | re.S)
    heading = re.search(r'<h1\b[^>]*>(.*?)</h1>', raw, re.I | re.S)
    if not match or not heading:
        return None
    try:
        published = datetime.fromisoformat(match[1].strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    assumed_timezone = ''
    if published.tzinfo is None:
        # 仅对这些已知中国大陆媒体的专用字段使用明确的出版地时区。
        assumed_timezone = 'Asia/Shanghai'
        published = published.replace(tzinfo=ZoneInfo(assumed_timezone))
    return {'title': heading[1], 'published_at': published.isoformat(),
            'date_timezone_assumed': assumed_timezone, 'date_basis': 'publisher_metadata'}
