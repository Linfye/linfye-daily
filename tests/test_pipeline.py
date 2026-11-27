import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from newsdaily.ai import AI, validate_scores
from newsdaily.collect import Fetcher, collect_source, item, parse_feed, parse_article, recent, canonical
from newsdaily.config import load_config, load_sources
from newsdaily.pipeline import build_report, save_report, choose, project_lock
from newsdaily.__main__ import due
from newsdaily.deploy import systemd_units, unit_quote

NOW = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)


def source(lang='en'):
    return {'id': 's-' + lang, 'name': 'Test ' + lang, 'language': lang,
            'url': 'https://example.test', 'kind': 'news'}


def article(i, lang='en'):
    title = f'公开报道：第 {i} 项科技政策' if lang == 'zh' else f'Public reporting about technology policy {i}'
    return item(source(lang), title, f'https://example.test/{lang}/{i}',
                '公开资料显示政策发生变化。' if lang == 'zh' else 'Public records describe a policy change.',
                NOW.isoformat())


def result(a):
    return {'id': a['id'], 'summary': '公开资料显示政策发生变化。' if a['language'] == 'zh' else 'Public records describe a policy change.',
            'importance': 40 if '<script>' in a['title'] else 30, 'credibility': 20, 'timeliness': 18, 'originality': 10,
            'international': a['language'] == 'en', 'origin': 'original', 'content_type': 'news',
            'event_key': a['id'], 'local_relevance': 'unknown', 'local_reason': ''}


class FakeAPI(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        type(self).calls += 1
        if self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer test-only':
            self.send_error(400)
            return
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        payload = json.loads(request['messages'][1]['content'])
        if isinstance(payload, dict) and 'articles' in payload:
            out = {'articles': [result(a) for a in payload['articles']]}
        else:
            out = {'groups': [[a['id']] for a in payload]}
        body = json.dumps({'choices': [{'message': {'content': json.dumps(out, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = load_config()
        self.cfg['data_dir'] = Path(self.tmp.name) / 'data'
        self.cfg['output_dir'] = Path(self.tmp.name) / 'output'

    def test_source_list_matches_user(self):
        sources = load_sources()
        self.assertEqual(len(sources), 50)
        self.assertEqual(sum(s['language'] == 'en' for s in sources), 20)
        self.assertEqual(sum(s.get('region') == '大陆' for s in sources), 10)
        self.assertEqual(sum(s.get('kind') == 'commentary' for s in sources), 10)

    def test_rss_atom_dates_and_cleaning(self):
        rss = b'''<rss><channel><item><title>A &amp; B</title><link>https://example.test/a?utm_source=x</link>
        <description>&lt;p&gt;One fact.&lt;/p&gt;</description><pubDate>Thu, 26 Nov 2026 14:00:00 GMT</pubDate></item></channel></rss>'''
        a = parse_feed(rss, source(), 'https://example.test/rss')[0]
        self.assertEqual(a['url'], 'https://example.test/a')
        self.assertEqual(a['excerpt'], 'One fact.')
        self.assertTrue(recent(a, NOW, 36))
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New report</title>
        <link href="/b"/><published>2026-11-20T14:00:00Z</published><updated>2026-11-26T14:00:00Z</updated></entry></feed>'''
        self.assertFalse(recent(parse_feed(atom, source(), 'https://example.test')[0], NOW, 36))

    def test_missing_timezone_or_future_is_excluded(self):
        for date in ['', '2026-11-26T14:00:00', '2026-11-28T14:00:00+00:00']:
            a = article(1)
            a['published_at'] = date
            self.assertFalse(recent(a, NOW, 36))

    def test_public_article_jsonld_and_metadata(self):
        raw = b'''<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle",
        "headline":"New policy report","datePublished":"2026-11-26T14:00:00Z","articleBody":"A public fact."}]}</script>'''
        a = parse_article(raw, source(), 'https://example.test/a')
        self.assertEqual(a['evidence'], 'public_article')
        raw = b'<meta property="og:title" content="New policy"><meta property="article:published_time" content="2026-11-26T14:00:00Z">'
        self.assertIsNotNone(parse_article(raw, source(), 'https://example.test/a'))
        self.assertIsNone(parse_article(b'<title>Category page</title>', source(), 'https://example.test/a'))

    def test_chinese_publisher_publication_dates(self):
        fixtures = [
            ('yicai', 'https://www.yicai.com/news/12345.html', "<script>['actime', '2026-11-26T21:54:35+08']</script>"),
            ('infzm', 'https://www.infzm.com/contents/12345', '<span class="nfzm-content__publish" data-time="2026-11-26 08:00:00">11-26</span>'),
            ('eeo', 'https://www.eeo.com.cn/2026/1126/12345.shtml', "<script>var publishedTime = '2026-11-26 11:13';</script>"),
            ('cyol', 'https://news.cyol.com/gb/news/articles/2026-11/26/content_abc.html', '<!--enpproperty <date>2026-11-26 20:39:03</date><title>测试</title>-->'),
            ('xinhua', 'https://www.news.cn/politics/20261126/abc/c.html', '<div class="info">2026-11-26 17:36:07<span>来源：公开资料</span></div>'),
        ]
        for sid, url, markup in fixtures:
            s = dict(source('zh'), id=sid)
            raw = ('<h1>本地公共政策测试报道</h1>' + markup).encode()
            a = parse_article(raw, s, url)
            self.assertIsNotNone(a, sid)
            self.assertTrue(recent(a, NOW, 36), sid)
            self.assertEqual(a['date_basis'], 'publisher_metadata')
            self.assertEqual(a['date_timezone_assumed'], '' if sid == 'yicai' else 'Asia/Shanghai')
            self.assertIsNone(parse_article(raw, s, 'https://unrelated.test/news/12345.html'))
        # 推荐文章或正文提到今天，不能把旧文章当作今天发布。
        raw = '<h1>旧报道</h1><p>参考日期 2026-11-26 12:00:00</p>'.encode()
        self.assertIsNone(parse_article(raw, dict(source('zh'), id='yicai'), fixtures[0][1]))

    def test_language_specific_candidate_limits(self):
        from newsdaily.collect import collect
        ss = [dict(source('zh'), id='zh-one'), dict(source('en'), id='en-one')]
        def fake_collect(s, cfg, now):
            aa = [dict(article(i, s['language']), source_id=s['id']) for i in range(60)]
            return aa, {'source_id': s['id'], 'source': s['name'], 'status': 'ok', 'count': 60, 'warnings': []}
        self.cfg['max_candidates_by_language'] = {'zh': 50, 'en': 25}
        with patch('newsdaily.collect.collect_source', fake_collect):
            aa, _ = collect(ss, self.cfg, NOW)
        self.assertEqual(sum(a['language'] == 'zh' for a in aa), 50)
        self.assertEqual(sum(a['language'] == 'en' for a in aa), 25)

    def test_rss_source_cap_can_exceed_old_15_article_limit(self):
        from newsdaily.collect import collect_source
        body = ''.join(f'<item><title>本地政策测试新闻第{i}篇</title><link>https://example.test/zh/{i}</link>'
                       '<pubDate>Thu, 26 Nov 2026 14:00:00 GMT</pubDate></item>' for i in range(65))
        raw = ('<rss><channel>' + body + '</channel></rss>').encode()
        s = dict(source('zh'), feeds=['https://example.test/rss'])
        self.cfg['max_articles_per_source_by_language'] = {'zh': 50}
        with patch.object(Fetcher, 'get', return_value=(raw, 'https://example.test/rss')):
            aa, status = collect_source(s, self.cfg, NOW)
        self.assertEqual(len(aa), 50)
        self.assertEqual(status['count'], 50)

    def test_score_validation_rejects_injection_and_wrong_language(self):
        a = article(1)
        r = result(a)
        r.update(title='INJECTED', url='javascript:alert(1)', language='zh')
        scored = validate_scores({'articles': [r]}, [a])[0]
        self.assertEqual(scored['title'], a['title'])
        self.assertEqual(scored['url'], a['url'])
        self.assertEqual(scored['language'], 'en')
        for field, value in [('summary', '错误语种'), ('importance', 100), ('international', 'false')]:
            row = result(a)
            row[field] = value
            with self.assertRaises(ValueError):
                validate_scores({'articles': [row]}, [a])
        with self.assertRaises(ValueError):
            validate_scores({'articles': [result(a), result(a)]}, [a])

    def test_event_dedup_prefers_english_original(self):
        aa = [article(1, 'zh'), article(1), article(2)]
        scored = validate_scores({'articles': [result(a) for a in aa]}, aa)
        scored[0].update(international=True, score=99)
        scored[1].update(origin='syndicated', score=95)
        scored[2].update(origin='original', score=75)
        selected, _ = choose(scored, [[a['id'] for a in aa]], 20, 4)
        self.assertEqual(len(selected['zh']), 0)
        self.assertEqual(selected['en'][0]['id'], scored[2]['id'])

    def test_local_chinese_news_preference_and_fallback(self):
        local, foreign, unknown = [article(i, 'zh') for i in range(3)]
        for a in (local, foreign, unknown):
            a['source_region'] = '马来西亚'
        rows = [result(a) for a in (local, foreign, unknown)]
        rows[0].update(local_relevance='local', local_reason='报道马来西亚本地预算政策。')
        rows[1].update(local_relevance='nonlocal')
        scored = validate_scores({'articles': rows}, [local, foreign, unknown])
        scored[0]['score'], scored[1]['score'], scored[2]['score'] = 78, 85, 80
        groups = [[a['id']] for a in scored]
        chosen, _ = choose(scored, groups, 3, 4)
        self.assertEqual([a['id'] for a in chosen['zh']], [local['id'], foreign['id'], unknown['id']])
        self.assertEqual(chosen['zh'][0]['score'], 78)  # 基础分不被加分覆盖。
        no_bonus, _ = choose(scored, groups, 1, 4, local_news_bonus=0)
        self.assertEqual(no_bonus['zh'][0]['id'], foreign['id'])
        scored[1]['score'] = 95  # 重大非本地新闻仍有入选空间。
        important, _ = choose(scored, groups, 1, 4)
        self.assertEqual(important['zh'][0]['id'], foreign['id'])

    def test_local_preference_applies_when_selecting_duplicate_story(self):
        aa = [article(10, 'zh'), article(11, 'zh')]
        for a in aa:
            a['source_region'] = '马来西亚'
        rows = [result(a) for a in aa]
        rows[0].update(local_relevance='local', local_reason='事件核心为马来西亚公共交通。')
        rows[1].update(local_relevance='nonlocal')
        scored = validate_scores({'articles': rows}, aa)
        scored[0].update(score=78, international=False)
        scored[1].update(score=85, international=False)
        chosen, _ = choose(scored, [[a['id'] for a in aa]], 20, 4)
        self.assertEqual([a['id'] for a in chosen['zh']], [aa[0]['id']])

    def test_locality_cannot_be_claimed_without_region_or_evidence(self):
        for lang, region in [('en', '马来西亚'), ('zh', ''), ('zh', '跨地区')]:
            a = article(20, lang)
            a['source_region'] = region
            row = result(a)
            row.update(local_relevance='local', local_reason='模型声称本地新闻。')
            with self.assertRaises(ValueError):
                validate_scores({'articles': [row]}, [a])
        a = article(21, 'zh')
        a['source_region'] = '马来西亚'
        row = result(a)
        row.update(local_relevance='local', local_reason='')
        with self.assertRaises(ValueError):
            validate_scores({'articles': [row]}, [a])

    def test_source_regions_reach_ai_without_model_override(self):
        sources = {s['id']: s for s in load_sources()}
        for sid, region in [('sinchew', '马来西亚'), ('zaobao', '新加坡'),
                            ('hk01', '香港'), ('cna', '台湾'), ('caixin', '大陆')]:
            a = item(sources[sid], '本地公共政策发布', 'https://example.test/' + sid,
                     '当地居民和公共政策的公开资料。', NOW.isoformat())
            self.assertEqual(a['source_region'], region)
            row = result(a)
            row.update(source_region='被模型篡改', local_relevance='local', local_reason='公开资料指向当地政策。')
            scored = validate_scores({'articles': [row]}, [a])[0]
            self.assertEqual(scored['source_region'], region)

    def test_scheduler_timezone_retry_and_day_rollover(self):
        self.assertTrue(due(NOW, self.cfg, {}))  # 09:00 Chicago
        self.assertFalse(due(NOW - timedelta(hours=2), self.cfg, {}))
        state = {'date': '2026-11-26', 'done': True, 'attempts': 1, 'last_attempt': NOW.isoformat()}
        self.assertFalse(due(NOW, self.cfg, state))
        state['done'] = False
        self.assertFalse(due(NOW + timedelta(minutes=10), self.cfg, state))
        self.assertTrue(due(NOW + timedelta(minutes=16), self.cfg, state))
        state['attempts'] = 3
        self.assertFalse(due(NOW + timedelta(hours=2), self.cfg, state))
        self.assertTrue(due(NOW + timedelta(days=1), self.cfg, state))
        # 夏令时 08:00 = 13:00 UTC，冬令时 08:00 = 14:00 UTC。
        self.assertTrue(due(datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc), self.cfg, {}))
        self.assertFalse(due(datetime(2026, 11, 26, 13, 0, tzinfo=timezone.utc), self.cfg, {}))

    def test_failed_sources_are_not_fake_success(self):
        disabled = dict(source('zh'), enabled=False, note='待填写链接')
        aa, status = collect_source(disabled, self.cfg, NOW)
        self.assertEqual(aa, [])
        self.assertEqual(status['status'], 'needs_config')
        class NoAI:
            pass
        report = build_report(self.cfg, [], NOW, collector=lambda *_: ([], [status]), ai=NoAI())
        self.assertFalse(report['complete'])
        self.assertEqual(report['articles'], {'zh': [], 'en': []})

    def test_robots_denial_prevents_article_fetch(self):
        calls = []
        def raw(url):
            calls.append(url)
            return b'User-agent: *\nDisallow: /private\n', url
        f = Fetcher()
        with patch.object(f, 'raw', raw):
            with self.assertRaises(ValueError):
                f.get('https://example.test/private/story')
        self.assertEqual(calls, ['https://example.test/robots.txt'])

    def test_lock_rejects_concurrent_run(self):
        with project_lock(self.cfg['data_dir'] / 'run.lock'):
            with self.assertRaises(RuntimeError):
                with project_lock(self.cfg['data_dir'] / 'run.lock'):
                    pass

    def test_end_to_end_http_api_cache_40_articles_and_safe_output(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeAPI)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        aa = [article(i, lang) for lang in ('zh', 'en') for i in range(24)]
        aa[0]['title'] = '<script>alert("x")</script>公开报道'
        statuses = [{'source_id': 'test', 'source': '测试源', 'status': 'ok', 'count': 48, 'warnings': []}]
        env = {'NEWS_API_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
               'NEWS_API_KEY': 'test-only', 'NEWS_API_MODEL': 'test-model'}
        with patch.dict(os.environ, env):
            ai = AI(self.cfg)
            report = build_report(self.cfg, [], NOW, collector=lambda *_: (aa, statuses), ai=ai)
            self.assertTrue(report['complete'])
            self.assertEqual([len(v) for v in report['articles'].values()], [20, 20])
            before = FakeAPI.calls
            ai.score(aa[:8])
            self.assertEqual(FakeAPI.calls, before)  # 命中缓存，无额外 API 费用。
        path = save_report(report, self.cfg)
        body = path.read_text(encoding='utf-8')
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('## English selection · 20/20', body)
        self.assertIn('Public records describe a policy change.', body)
        self.assertEqual(path.name, '2026-11-26.md')
        self.assertEqual(list(self.cfg['output_dir'].iterdir()), [path])
        saved = json.loads((self.cfg['data_dir'] / 'reports' / '2026-11-26.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['articles']['en']), 20)
        self.assertEqual(save_report(report, self.cfg), path)
        self.assertEqual(list(self.cfg['output_dir'].iterdir()), [path])
        next_day = dict(report, date='2026-11-27')
        save_report(next_day, self.cfg)
        self.assertEqual(sorted(p.name for p in self.cfg['output_dir'].iterdir()),
                         ['2026-11-26.md', '2026-11-27.md'])

    def test_systemd_timer_uses_existing_due_scheduler(self):
        units = systemd_units(Path('/srv/linfye daily'))
        service, timer = units['linfye-daily.service'], units['linfye-daily.timer']
        self.assertIn(' -m newsdaily due', service)
        self.assertIn('Type=oneshot', service)
        self.assertIn('SuccessExitStatus=2', service)
        self.assertIn('Persistent=true', timer)
        self.assertIn('OnCalendar=*-*-* *:00/5:00', timer)
        self.assertNotIn('America/Chicago', timer)  # 出报时区由 Python 配置控制。

    def test_systemd_path_quoting(self):
        self.assertEqual(unit_quote('/srv/日报 100%'), '"/srv/日报 100%%"')
        self.assertEqual(unit_quote('/srv/$folder', executable=True), '"/srv/$$folder"')
        with self.assertRaises(ValueError):
            unit_quote('/srv/bad\nExecStart=malicious')


if __name__ == '__main__':
    unittest.main()
