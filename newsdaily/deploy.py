"""ARM/x86 Linux 用户级 systemd 安装工具。"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT


def unit_quote(value, executable=False):
    value = str(value)
    if any(c in value for c in ('\n', '\r', '\x00')):
        raise ValueError('systemd 路径不能包含换行或 NUL')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if executable:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def systemd_units(root):
    root = Path(root)
    service = f'''[Unit]
Description=Linfye daily Markdown news digest

[Service]
Type=oneshot
WorkingDirectory={unit_quote(root)}
ExecStart={unit_quote(root / '.venv/bin/python', executable=True)} -m newsdaily due
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
TimeoutStartSec=2h
SuccessExitStatus=2
UMask=0077
NoNewPrivileges=true
Nice=10
'''
    timer = '''[Unit]
Description=Check Linfye news schedule every five minutes

[Timer]
OnCalendar=*-*-* *:00/5:00
AccuracySec=1s
Persistent=true
Unit=linfye-daily.service

[Install]
WantedBy=timers.target
'''
    return {'linfye-daily.service': service, 'linfye-daily.timer': timer}


def main():
    parser = argparse.ArgumentParser(description='安装 ARM/Linux 用户级 systemd 日报定时器')
    parser.add_argument('--dry-run', action='store_true', help='只显示单元，不写文件或启用任务')
    args = parser.parse_args()
    units = systemd_units(ROOT)
    if args.dry_run:
        for name, content in units.items():
            print(f'# {name}\n{content}')
        return
    if not sys.platform.startswith('linux'):
        raise RuntimeError('systemd 安装仅支持 Linux；可用 --dry-run 查看配置')
    if os.geteuid() == 0:
        raise RuntimeError('请以运行日报的普通用户执行，不要使用 sudo')
    if not (ROOT / '.venv/bin/python').exists():
        raise RuntimeError('请先在服务器本机创建 .venv 并安装 requirements.txt')
    if not (ROOT / '.env').exists():
        raise RuntimeError('请先运行 .venv/bin/python -m newsdaily setup')
    subprocess.run(['systemctl', '--user', 'show-environment'], check=True, stdout=subprocess.DEVNULL)
    config_home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    if not config_home.is_absolute():
        raise ValueError('XDG_CONFIG_HOME 必须为绝对路径')
    unit_dir = config_home / 'systemd/user'
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        (unit_dir / name).write_text(content, encoding='utf-8', newline='\n')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', 'linfye-daily.timer'], check=True)
    print('定时器已启用。每天出报时间由 config.json 控制。')
    print('服务器退出 SSH 后持续运行：sudo loginctl enable-linger "$USER"')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        print(f'安装失败：{e}', file=sys.stderr)
        sys.exit(1)
