# Linfye 日报 · ARM 服务器版

每天抓取指定媒体、通过可配置的 AI API 评分与事件去重，将 **20 篇中文 + 20 篇英文** 合并为一篇 Markdown 日报。内容为原新闻标题、链接、源语言简短摘要，国际新闻优先英文一手报道。已录入你提供的 40 家媒体和 10 个自媒体。

## ARM Linux 部署

使用 Python 3.11+，业务逻辑只依赖标准库，唯一 pip 依赖为纯 Python 时区数据 `tzdata`，没有 x86 原生扩展或本地大模型依赖。适用于安装了相应版本 Python 的 ARM64/aarch64 Linux，也可在 ARMv7 或 x86 Linux 上原生运行。本次开发环境为 Windows，尚未在你的 ARM 服务器上实机验证。

将项目代码复制到服务器，例如 `~/linfye-daily`。**不要上传 Windows 的 `.venv`**；在服务器本机重建虚拟环境。输出与内部状态可按需要保留，密钥请在服务器重新填写。

Debian 12+/Ubuntu 24.04 的示例（其他发行版使用自己的包管理器安装 Python 3.11+）：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ca-certificates
cd ~/linfye-daily
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m newsdaily setup
.venv/bin/python -m newsdaily test-api
.venv/bin/python -m newsdaily run
ls -lh output/*.md
```

`setup` 提示输入 API Base URL、模型 ID、API Key，密钥隐藏输入并保存到项目 `.env`（Linux 权限为 600，已加入 `.gitignore`）。也可复制 `.env.example` 为 `.env` 后手动填写，执行 `chmod 600 .env`。无需图形界面、浏览器或 Node.js。

API 需兼容 `POST {BASE_URL}/chat/completions`。Base URL 通常填到 `/v1`，不要包含 `/chat/completions`。模型 ID 按你的服务商填写。远程 API 使用 HTTPS，localhost 本地服务允许 HTTP。文章标题、摘要及公开正文会发送至该 API，API 调用按供应商计费。

## 每天定时运行

默认每天 **08:00，America/Chicago**。编辑 `config.json` 可切换到北京时间：

```json
"schedule": {"time": "08:00", "timezone": "Asia/Shanghai", "retry_minutes": 15}
```

程序按此 IANA 时区判断当天日期与出报时刻，自动处理夏令时，与服务器自身时区无关。下面三种方式选择一种，避免重复调度。

### 推荐：systemd 用户定时器

先完成 API 配置和手动出报测试。以部署项目的普通用户在 SSH 会话内执行，安装命令不要加 sudo：

```bash
cd ~/linfye-daily
.venv/bin/python -m newsdaily.deploy
sudo loginctl enable-linger "$USER"
systemctl --user list-timers linfye-daily.timer
```

安装器自动把项目绝对路径写入 `~/.config/systemd/user/linfye-daily.service` 和 `.timer`（遵循 `XDG_CONFIG_HOME`），启用定时器。`enable-linger` 让用户服务在退出 SSH 后以及服务器启动时继续运行；这是管理员执行的一次性设置。无需保持终端在线。仅想预览配置：

```bash
.venv/bin/python -m newsdaily.deploy --dry-run
```

定时器每 5 分钟调用一次 `due`，程序到设定时间才生成日报，通常延迟不超过 5 分钟。当天重启后会补跑当天尚未完成的日报，不补发过去多天。修改 `config.json` 的时间、媒体名单或 API 后，下次检查自动读取；移动项目目录后应重新运行安装器。

```bash
# 查看日志和运行状态
journalctl --user -u linfye-daily.service -n 100 --no-pager
systemctl --user status linfye-daily.timer

# 立即检查当天是否到期（不会强制重复成功日报）
systemctl --user start linfye-daily.service

# 停用调度
systemctl --user disable --now linfye-daily.timer
```

安装报 `Failed to connect to bus` 时，确认服务器有 systemd 用户会话，执行驻留设置后重新 SSH 登录，或改用下面的 cron。Alpine/OpenRC、部分容器和 NAS 系统可以直接使用 cron 或系统自带任务计划。

### 备选：cron

运行 `crontab -e`，加入下面一行。把两处路径改成项目真实绝对路径，下面使用 `/home/ubuntu/linfye-daily` 作为示例：

```cron
*/5 * * * * cd /home/ubuntu/linfye-daily && /home/ubuntu/linfye-daily/.venv/bin/python -u -m newsdaily due >> /home/ubuntu/linfye-daily/data/cron.log 2>&1
```

先执行 `mkdir -p data`。cron 每 5 分钟检查一次，实际出报时区仍由 `config.json` 决定；日志需按服务器惯例配置轮转。

### 备选：常驻进程

```bash
.venv/bin/python -u -m newsdaily schedule
```

该模式需自行用进程管理器保持运行。单次任务可使用 `run` 强制出报，或 `due` 按调度条件执行。失败或文章不足时，每 15 分钟重试，每天最多 3 次；当天完整成功后不再定时重复执行。使用跨进程文件锁避免重叠，Linux 使用 `fcntl`，Windows 保留兼容。

## 一篇 Markdown 输出

每个出报日只在发布目录写入 **`output/YYYY-MM-DD.md`**，中英文各一节、按分数排序。同日重跑原子替换该文档，历史日期文件保留。不再生成 HTML、发布用 JSON 或 `latest.md` 副本。升级前已有文件不会自动删除。

文章示意：

```markdown
# Linfye 日报 · 2026-11-26

## 中文精选 · 20/20

1. [新闻源原标题](<https://example.com/article>)

   使用新闻源语言的简短摘要。

   媒体名称 · 新闻 · 88/100 · RSS / 公开摘要

## English selection · 20/20

1. [Original headline](<https://example.com/story>)

   A short summary in the source language.

   Publisher · news · 90/100 · RSS / public excerpt
```

这是格式示意，不是真实新闻。正文不堆放 50 个来源的抓取错误；候选不足及来源失败的汇总会在顶部提示，完整来源状态与评分明细保存在 `data/reports/YYYY-MM-DD.json`，供排错使用。

- `data/schedule.json`：当天调度次数、完成状态、最近错误。
- `data/cache/`：AI 分批评分缓存，可降低同日重试费用。
- `data/candidates.json`：执行 `fetch` 后的候选与来源状态。

退出码：0 为成功或尚未到期；2 为已出报但数量不足或部分评分失败；1 为配置、API 等致命错误。致命错误保留上次成功写入的文档，用文件日期及文档生成时间判断新鲜度。systemd 接受退出码 2，并由后续定时检查执行有限重试。

## 来源与选编

编辑 `sources.json` 管理 50 个来源。`language` 为 `zh` / `en`；自媒体使用 `kind: "commentary"`，计入中文配额。9 个自媒体尚缺官方账号/RSS，保留为 `enabled: false`；填写官方公开文章列表 `url` 或 `feeds` 后改为 `true`。阮一峰博客已有 Atom。音视频来源只读公开标题/摘要，不做转录。

抓取优先使用 RSS/Atom，其次发现页面 RSS，再尝试站内公开文章的 JSON-LD 或文章发布时间元数据。默认回看 36 小时、每源 90 秒预算。中文每源最多 50 篇、最多 300 个 AI 候选；英文每源最多 15 篇、最多 100 个 AI 候选。候选数与最终精选数分开，最终默认仍为每种语言 20 篇。扩大候选会增加 AI 调用次数与费用，实际抓取数量受来源可用性及当天新闻量影响。

第一财经、南方周末、经济观察报、中青在线、新华网使用经过公开页面核对的专用发布时间解析。对于这些大陆媒体页面中明确的、无时区的出版时间，使用 `Asia/Shanghai`，诊断中记录 `date_timezone_assumed`；其他未知无时区日期继续跳过，不用正文或推荐栏提到的日期代替发布时间。扫描文章列表时优先站内文章 URL，并给无效链接留出额外扫描空间，避免前 15 个栏目或旧闻耗尽配额。中央社改用官网公布的政治、地方、生活 RSS 分类。

如需自行控制采集预算，在 `config.json` 顶层添加（旧配置无需修改即可采用新默认）：

```json
"max_candidates_by_language": {"zh": 300, "en": 100},
"max_articles_per_source_by_language": {"zh": 50, "en": 15}
```

部分媒体受 robots、403、付费或 JS 渲染限制；不会绕过限制。没有可靠日期、过期或重复的文章不补入配额。Atom 缺少 published 时可使用 updated，诊断中会标注。

AI 评分为重要性 40、可核查性 25、时效 20、原创性 15。对跨语言同一国际事件，优先选择有一手证据的英文报道，再选其他英文报道；一手判断依据输入资料，不等于独立事实核查。新闻、分析与评论分别标注，摘要使用新闻源语言，保留原标题和链接。每源默认软上限 4 篇，不足时放宽并提示；去重后不足 20 篇时如实输出数量，不用旧闻或虚构内容补足。

**中文优先媒体所在地的本地新闻。** 星洲日报优先马来西亚、联合早报优先新加坡、香港媒体优先香港、台湾媒体优先台湾、大陆媒体优先中国大陆。AI 根据文章资料和 `sources.json` 中的 `region` 判断本地相关性，并记录依据；不会仅凭媒体名称就把全部文章当成本地新闻。没有固定所在地的跨地区媒体及未配置地域的自媒体不获得地域加分。

本地新闻在选编排序时默认加 12 分，页面展示的基础 AI 评分仍为 100 分制。可在 `config.json` 顶层添加 `"local_news_bonus": 12` 调整（0–30，设为 0 关闭）；旧配置不必修改。此偏好也用于非国际事件的重复稿择优，不影响英文文章排序或国际事务优先英文源的规则。纯本地内部事务不因发生在中国大陆以外就被当作国际事务。本地合格候选不足时可由其他中文报道补足，不为地域偏好强行凑数。

地域分类、判定依据与基础分保留在当日内部诊断中；新提示版本会自动跳过旧评分缓存，无需手工清空。

```bash
.venv/bin/python -m newsdaily sources  # 查看来源名单
.venv/bin/python -m newsdaily fetch    # 仅抓取，不调用 AI
.venv/bin/python -m newsdaily run      # 立即评分并写一篇日报
```

## 测试与兼容

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖本地 HTTP 模拟 API 的 20+20 完整出报、单一 Markdown 文件、缓存、摘要语言、时效、跨语言去重、定时重试、文件锁及 systemd 配置生成。模拟数据只用于测试；未提供密钥时无法验证真实 AI 出报。本地没有 ARM Linux/systemd，部署后的首次手动运行及定时器检查仍需在服务器执行。

Windows 开发环境仍可使用 `.venv/Scripts/python.exe` 运行，`scripts/start.ps1` 菜单会打开最新日期的 Markdown。服务器端不依赖这些 PowerShell 脚本。

参考：[systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)、[loginctl](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html)、[Python venv](https://docs.python.org/3/library/venv.html)。
