import html
import re
from urllib.parse import quote


def markdown_text(value):
    # 外部文本按文字呈现，避免 Markdown 阅读器执行内嵌 HTML。
    text = html.escape(' '.join(str(value).split()), quote=False)
    return re.sub(r'([\\`*_{}\[\]#!|])', r'\\\1', text)


def render_markdown(r):
    e = markdown_text
    lines = [f'# Linfye 日报 · {r["date"]}', '',
             f'生成时间：{e(r["generated_at"])} · 时区：{e(r["timezone"])} · 回看 {r["lookback_hours"]} 小时。', '']
    for warning in r['warnings']:
        lines.extend(['> ' + e(warning), ''])
    for lang, title in [('zh', '中文精选'), ('en', 'English selection')]:
        lines.extend([f'## {title} · {len(r["articles"][lang])}/{r["target"]}', ''])
        for i, a in enumerate(r['articles'][lang], 1):
            url = quote(a['url'], safe=":/?#[]@!$&'()*+,;=%~")
            kind = ({'news': '新闻', 'analysis': '分析', 'commentary': '评论 / 解读'}[a['content_type']]
                    if lang == 'zh' else a['content_type'])
            evidence = ('公开正文' if a['evidence'] == 'public_article' else 'RSS / 公开摘要') if lang == 'zh' else (
                'public article' if a['evidence'] == 'public_article' else 'RSS / public excerpt')
            lines.extend([f'{i}. [{e(a["title"])}](<{url}>)', '',
                          f'   {e(a["summary"])}', '',
                          f'   {e(a["source"])} · {e(kind)} · {a["score"]}/100 · {e(evidence)}', ''])
    lines.extend(['---', '', '摘要保留新闻源语言；AI 评分仅供选编参考。完整来源状态保存在当日内部诊断记录中。', ''])
    return '\n'.join(lines)
