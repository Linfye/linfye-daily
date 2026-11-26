import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .ai import AI, atomic_json
from .collect import collect
from .config import ROOT, load_config, load_env, load_sources
from .pipeline import build_report, save_report, project_lock


def due(now, cfg, state):
    local = now.astimezone(ZoneInfo(cfg['schedule']['timezone']))
    date = local.date().isoformat()
    if local.strftime('%H:%M') < cfg['schedule']['time']:
        return False
    if state.get('date') != date:
        return True
    if state.get('done') or state.get('attempts', 0) >= 3:
        return False
    last = datetime.fromisoformat(state['last_attempt'])
    return now >= last + timedelta(minutes=cfg['schedule']['retry_minutes'])


def run_due(cfg, sources):
    with project_lock(cfg['data_dir'] / 'run.lock'):
        path = cfg['data_dir'] / 'schedule.json'
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        now = datetime.now(timezone.utc)
        if not due(now, cfg, state):
            return 0
        date = now.astimezone(ZoneInfo(cfg['schedule']['timezone'])).date().isoformat()
        state = dict(date=date, attempts=(state.get('attempts', 0) if state.get('date') == date else 0) + 1,
                     last_attempt=now.isoformat(), done=False)
        atomic_json(path, state)  # 即使进程崩溃也保留重试计数。
        try:
            report = build_report(cfg, sources, now)
            report_path = save_report(report, cfg)
            state['done'] = report['complete']
            state['error'] = ''
        except Exception as e:
            state['error'] = str(e)
            raise
        finally:
            atomic_json(path, state)
        print(f'日报：{report_path}；完整：{state["done"]}', flush=True)
        return 0 if state['done'] else 2


def setup():
    path = ROOT / '.env'
    if path.exists():
        print('将更新本地 .env；直接回车保留已有值。')
    load_env()
    base = input('API Base URL（包含 /v1；回车使用现有值或 OpenAI 默认值）：').strip()
    model = input('模型 ID（按服务商填写）：').strip()
    key = getpass.getpass('API Key（隐藏输入）：').strip()
    values = {'NEWS_API_BASE_URL': base or os.environ.get('NEWS_API_BASE_URL', 'https://api.openai.com/v1'),
              'NEWS_API_MODEL': model or os.environ.get('NEWS_API_MODEL', ''),
              'NEWS_API_KEY': key or os.environ.get('NEWS_API_KEY', '')}
    if any('\n' in v or '\r' in v for v in values.values()):
        raise ValueError('配置不能包含换行')
    if not values['NEWS_API_MODEL'] or not values['NEWS_API_KEY']:
        raise ValueError('需要填写模型和 API 密钥')
    os.environ.update(values)
    AI(load_config())  # 写入前校验地址。
    path.write_text('\n'.join(f'{k}={v}' for k, v in values.items()) + '\n', encoding='utf-8')
    if os.name != 'nt':
        path.chmod(0o600)
    print('配置已保存在本地 .env（已加入 .gitignore）。用 python -m newsdaily test-api 检查连接。')


def main():
    parser = argparse.ArgumentParser(description='Linfye 每日中英文 AI 新闻选编')
    parser.add_argument('command', choices=['setup', 'run', 'fetch', 'sources', 'schedule', 'due', 'test-api'])
    args = parser.parse_args()
    if args.command == 'setup':
        setup()
        return 0
    load_env()
    cfg, sources = load_config(), load_sources()
    if args.command == 'sources':
        for s in sources:
            print(f"{s['id']:16} {s['language']} {s['name']} {'[待配置]' if not s.get('enabled', True) else ''}")
        return 0
    if args.command == 'test-api':
        ai = AI(cfg)
        result = ai.request('只返回 JSON 对象 {"ok": true}。', {'test': True})
        if result != {'ok': True}:
            raise ValueError('API 已响应，但 JSON 兼容性测试失败')
        print('API 连接和 JSON 返回正常。')
        return 0
    if args.command == 'fetch':
        with project_lock(cfg['data_dir'] / 'run.lock'):
            articles, statuses = collect(sources, cfg, datetime.now(timezone.utc))
            atomic_json(cfg['data_dir'] / 'candidates.json', {'articles': articles, 'sources': statuses})
        print(f'抓取 {len(articles)} 篇；结果和来源状态：{cfg["data_dir"] / "candidates.json"}')
        return 0
    if args.command == 'due':
        return run_due(cfg, sources)
    if args.command == 'schedule':
        AI(cfg)
        print(f'等待每日 {cfg["schedule"]["time"]} {cfg["schedule"]["timezone"]}；需保持进程运行。', flush=True)
        while True:
            try:
                run_due(cfg, sources)
            except Exception as e:
                print(f'定时任务失败：{e}', file=sys.stderr, flush=True)
            time.sleep(30)
    with project_lock(cfg['data_dir'] / 'run.lock'):
        report = build_report(cfg, sources)
        path = save_report(report, cfg)
    print(f'日报：{path}；中文 {len(report["articles"]["zh"])} 篇，英文 {len(report["articles"]["en"])} 篇。')
    return 0 if report['complete'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('已停止。')
        sys.exit(130)
    except Exception as e:
        print(f'错误：{e}', file=sys.stderr)
        sys.exit(1)
