import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def load_env(root=ROOT):
    path = root / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_config(root=ROOT):
    cfg = json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))
    # 兼容服务器已有配置，不要求用户覆盖其出报时间设置。
    cfg.setdefault('local_news_bonus', 12)
    bonus = cfg['local_news_bonus']
    if isinstance(bonus, bool) or not isinstance(bonus, (int, float)) or not 0 <= bonus <= 30:
        raise ValueError('local_news_bonus 必须为 0–30 的数字')
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', cfg['schedule']['time']):
        raise ValueError('schedule.time 必须为 HH:MM')
    ZoneInfo(cfg['schedule']['timezone'])
    for key in ('lookback_hours', 'count_per_language', 'max_candidates_per_language',
                'max_articles_per_source', 'max_per_source_in_report', 'workers',
                'request_timeout_seconds', 'source_timeout_seconds', 'ai_timeout_seconds', 'ai_batch_size'):
        if not isinstance(cfg[key], int) or isinstance(cfg[key], bool) or cfg[key] <= 0:
            raise ValueError(f'{key} 必须为正整数')
    if not isinstance(cfg['ai_retries'], int) or not 0 <= cfg['ai_retries'] <= 5:
        raise ValueError('ai_retries 必须在 0–5 之间')
    if cfg['schedule']['retry_minutes'] < 1:
        raise ValueError('retry_minutes 必须大于 0')
    if cfg['max_candidates_per_language'] < cfg['count_per_language']:
        raise ValueError('候选数量不能小于目标数量')
    cfg.setdefault('max_candidates_by_language', {'zh': max(300, cfg['max_candidates_per_language']),
                                                'en': cfg['max_candidates_per_language']})
    cfg.setdefault('max_articles_per_source_by_language', {'zh': max(50, cfg['max_articles_per_source']),
                                                         'en': cfg['max_articles_per_source']})
    for key in ('max_candidates_by_language', 'max_articles_per_source_by_language'):
        if not isinstance(cfg[key], dict) or set(cfg[key]) - {'zh', 'en'}:
            raise ValueError(f'{key} 必须为包含 zh/en 的配置对象')
        for lang, value in cfg[key].items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f'{key}.{lang} 必须为正整数')
            if key == 'max_candidates_by_language' and value < cfg['count_per_language']:
                raise ValueError(f'{key}.{lang} 候选数量不能小于目标数量')
    for key in ('data_dir', 'output_dir'):
        path = (root / cfg[key]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f'{key} 必须位于项目目录内')
        cfg[key] = path
    return cfg


def load_sources(root=ROOT):
    sources = json.loads((root / 'sources.json').read_text(encoding='utf-8-sig'))
    ids = set()
    for s in sources:
        if s['id'] in ids or s['language'] not in ('zh', 'en'):
            raise ValueError('媒体 ID 必须唯一，language 只支持 zh/en')
        ids.add(s['id'])
        for url in [s.get('url', ''), *s.get('feeds', [])]:
            if url and urlsplit(url).scheme not in ('http', 'https'):
                raise ValueError(f"{s['name']} URL 必须为 HTTP(S)")
    return sources
