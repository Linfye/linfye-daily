import contextlib
import json
import os
from datetime import datetime, timezone

from .ai import AI, atomic_json
from .collect import collect
from .render import render_markdown


@contextlib.contextmanager
def project_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    try:
        # Windows 的字节锁同时禁止其他句柄读取锁定字节，先检查长度而非读取。
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError('已有任务正在运行') from None
    try:
        yield
    finally:
        f.seek(0)
        if os.name == 'nt':
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def choose(articles, groups, count, max_per_source, local_news_bonus=12):
    def rank(a):
        local = (a['language'] == 'zh' and a.get('source_region', '') not in ('', '跨地区')
                 and a.get('local_relevance') == 'local')
        return a['score'] + (local_news_bonus if local else 0)

    by_id = {a['id']: a for a in articles}
    winners = []
    for group in groups:
        candidates = [by_id[i] for i in group]
        # 对同一国际事件，优先有明确一手证据的英文报道，其次英文其他报道。
        # 判断为 unknown 时绝不冒称已核实一手来源。
        international = any(a['international'] for a in candidates)
        if international and any(a['language'] == 'en' for a in candidates):
            candidates = [a for a in candidates if a['language'] == 'en']
            candidates.sort(key=lambda a: (a['origin'] == 'original', a['score']), reverse=True)
        else:
            candidates.sort(key=rank, reverse=True)
        winners.append(candidates[0])
    winners.sort(key=lambda a: (rank(a), a['published_at'], a['id']), reverse=True)
    result, relaxed = {'zh': [], 'en': []}, []
    for lang in ('zh', 'en'):
        pool = [a for a in winners if a['language'] == lang]
        counts, deferred = {}, []
        for a in pool:
            if len(result[lang]) >= count:
                break
            if counts.get(a['source_id'], 0) >= max_per_source:
                deferred.append(a)
            else:
                result[lang].append(a)
                counts[a['source_id']] = counts.get(a['source_id'], 0) + 1
        for a in deferred:
            if len(result[lang]) >= count:
                break
            result[lang].append(a)
            relaxed.append(lang)
        result[lang].sort(key=rank, reverse=True)
    return result, sorted(set(relaxed))


def atomic_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(value, encoding='utf-8')
    tmp.replace(path)


def build_report(cfg, sources, now=None, collector=collect, ai=None):
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    ai = ai or AI(cfg, now=now)  # 缺少 API 时在抓取前提示。
    articles, statuses = collector(sources, cfg, now)
    scored, errors = [], []
    size = cfg['ai_batch_size']
    for start in range(0, len(articles), size):
        try:
            scored.extend(ai.score(articles[start:start + size]))
        except RuntimeError as e:
            errors.append(f'评分批次 {start // size + 1}: {e}')
        print(f'AI 评分 {min(start + size, len(articles))}/{len(articles)}', flush=True)
    if scored:
        # 全局事件去重失败时终止，避免静默产生多语种重复日报。
        groups = ai.clusters(scored)
        chosen, relaxed = choose(scored, groups, cfg['count_per_language'], cfg['max_per_source_in_report'],
                                 cfg.get('local_news_bonus', 12))
    else:
        chosen, relaxed = {'zh': [], 'en': []}, []
    warnings = list(errors)
    for lang, title in [('zh', '中文'), ('en', '英文')]:
        n = len(chosen[lang])
        if n < cfg['count_per_language']:
            warnings.append(f'{title}仅有 {n}/{cfg["count_per_language"]} 篇符合时效、语言及去重条件的文章，未用旧闻补足。')
        if lang in relaxed:
            warnings.append(f'{title}候选不足，已放宽单一媒体 {cfg["max_per_source_in_report"]} 篇的软上限。')
    failed = sum(s['status'] in ('unavailable', 'partial') for s in statuses)
    pending = sum(s['status'] == 'needs_config' for s in statuses)
    if failed:
        warnings.append(f'{failed} 个源抓取失败或部分失败，详见来源状态。')
    if pending:
        warnings.append(f'{pending} 个自媒体尚待配置官方账号/RSS。')
    report = dict(date=now.astimezone(ZoneInfo(cfg['schedule']['timezone'])).date().isoformat(),
                  generated_at=now.isoformat(), timezone=cfg['schedule']['timezone'],
                  lookback_hours=cfg['lookback_hours'], target=cfg['count_per_language'],
                  candidate_count=len(articles), scored_count=len(scored),
                  complete=not errors and all(len(v) == cfg['count_per_language'] for v in chosen.values()),
                  articles=chosen, warnings=warnings, sources=statuses)
    return report


def save_report(report, cfg):
    path = cfg['output_dir'] / (report['date'] + '.md')
    # 发布目录每天仅一份 Markdown；完整诊断留在内部数据目录。
    atomic_json(cfg['data_dir'] / 'reports' / (report['date'] + '.json'), report)
    atomic_text(path, render_markdown(report))
    return path
