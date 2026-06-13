#!/usr/bin/env python3
"""
Web 面板 — 浏览 Bilibili 视频处理数据库。

启动:
    python3 webui.py [--port 8686]

页面:
    /           视频列表
    /search?q=  搜索
    /video/<bv> 视频详情
    /stats      统计
"""
import argparse
from pathlib import Path

from flask import Flask, render_template_string, request

from db import DB

app = Flask(__name__)


def layout(content: str, q: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bilibili Pipeline</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#f5f5f5; color:#333; max-width:1000px; margin:0 auto; padding:20px; }}
  nav {{ display:flex; gap:20px; align-items:center; margin-bottom:24px;
        padding:12px 20px; background:#fff; border-radius:10px; box-shadow:0 1px 3px rgba(0,0,0,.1); }}
  nav a {{ color:#1a73e8; text-decoration:none; font-weight:500; }}
  nav a:hover {{ text-decoration:underline; }}
  nav form {{ margin-left:auto; display:flex; gap:8px; }}
  nav input[type=text] {{ padding:6px 12px; border:1px solid #ddd; border-radius:6px; font-size:14px; width:200px; }}
  nav button {{ padding:6px 16px; background:#1a73e8; color:#fff; border:none; border-radius:6px; cursor:pointer; }}
  .card {{ background:#fff; border-radius:10px; padding:16px 20px; margin-bottom:12px;
          box-shadow:0 1px 3px rgba(0,0,0,.1); }}
  .card h3 {{ margin-bottom:4px; font-size:16px; }}
  .card a {{ color:#1a73e8; text-decoration:none; }}
  .card a:hover {{ text-decoration:underline; }}
  .card .meta {{ font-size:13px; color:#666; margin-top:4px; }}
  .card .brief {{ font-size:14px; color:#444; margin-top:8px; padding:8px 12px;
                 background:#f8f9fa; border-radius:6px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ padding:8px 12px; text-align:left; border-bottom:1px solid #eee; font-size:14px; }}
  th {{ font-weight:600; color:#555; }}
  .stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:12px; }}
  .stat-card {{ background:#fff; border-radius:10px; padding:20px; text-align:center;
               box-shadow:0 1px 3px rgba(0,0,0,.1); }}
  .stat-card .num {{ font-size:28px; font-weight:700; color:#1a73e8; }}
  .stat-card .label {{ font-size:13px; color:#666; margin-top:4px; }}
  .detail-section {{ margin-top:16px; }}
  .detail-section h3 {{ font-size:15px; color:#555; margin-bottom:8px; border-bottom:1px solid #eee; padding-bottom:4px; }}
  pre {{ white-space:pre-wrap; font-size:13px; line-height:1.6; background:#f8f9fa;
        padding:12px; border-radius:6px; max-height:300px; overflow-y:auto; }}
</style>
</head>
<body>
<nav>
  <a href="/">📋 视频列表</a>
  <a href="/collections">📁 收藏夹</a>
  <a href="/stats">📊 统计</a>
  <form action="/search" method="get">
    <input type="text" name="q" placeholder="搜索 BV/标题/UP主..." value="{q}">
    <button>搜索</button>
  </form>
</nav>
<main>
{content}
</main>
</body>
</html>"""


def format_duration(sec):
    if not sec:
        return "-"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def fmt_cost(c):
    return f"¥{c:.4f}" if c else "¥0"


@app.route("/")
def index():
    db = DB()
    videos = db.list_videos()
    db.close()

    def status_badge(status, model=""):
        if status == "done":
            return f'<span style="background:#e8f5e9;color:#2e7d32;padding:2px 6px;border-radius:4px;font-size:11px;">✓ {model or "done"}</span>'
        return f'<span style="background:#f5f5f5;color:#999;padding:2px 6px;border-radius:4px;font-size:11px;">pending</span>'

    cards = ""
    for v in videos:
        total = (v.get("summary_cost", 0) or 0) + (v.get("transcript_cost", 0) or 0) + (v.get("visual_cost", 0) or 0)
        brief = v.get("brief", "") or ""
        badges = f'{status_badge(v.get("t_status",""), v.get("t_model",""))} {status_badge(v.get("s_status",""), v.get("s_model",""))} {status_badge(v.get("v_status",""), v.get("v_model",""))}'
        brief_html = f'<div class="brief">{brief[:200]}{"…" if len(brief) > 200 else ""}</div>' if brief else ""
        src = v.get("source_uid", "") or v.get("source", "") or ""
        cards += f"""<div class="card">
    <h3><a href="/video/{v['bv']}">{v['title']}</a></h3>
    <div class="meta">{v['bv']} · {v.get('uploader','')} · {format_duration(v.get('duration_sec'))} · {fmt_cost(total)} · {badges}</div>
    {f'<div style="font-size:11px;color:#aaa;margin-top:2px;">{src}</div>' if src else ''}
    {brief_html}
</div>
"""
    if not cards:
        cards = '<div class="card" style="text-align:center;color:#999;padding:40px;">暂无数据</div>'

    return layout(cards)


@app.route("/video/<bv>")
def video_detail(bv):
    db = DB()
    v = db.get_video_detail(bv)
    db.close()
    if not v:
        return layout("<h2>未找到</h2>"), 404

    desc_section = ""
    if v.get("description"):
        desc_section = f'<div class="detail-section"><h3>简介</h3><pre>{v["description"]}</pre></div>'

    def status_text(status, model=""):
        if status == "done":
            return f"✓ {model}" if model else "✓"
        return f"· {status}"

    status_row = (
        f'转录: {status_text(v.get("t_status",""), v.get("t_model",""))} · '
        f'汇总: {status_text(v.get("s_status",""), v.get("s_model",""))} · '
        f'视觉: {status_text(v.get("v_status",""), v.get("v_model",""))}'
    )

    sections = f"""<div class="card">
  <h2>{v['title']}</h2>
  <div class="meta" style="margin:8px 0;">
    <a href="{v['url']}" target="_blank">{v['bv']}</a>
    · {v.get('uploader','')} · {format_duration(v.get('duration_sec'))} · {v.get('created_at','')}
  </div>
  <div class="meta" style="font-size:12px;">{status_row}</div>
  <div class="meta" style="font-size:12px;">来源: {v.get('source_uid','') or v.get('source','') or '-'}</div>
  {desc_section}
</div>"""

    if v.get("summaries"):
        items = ""
        for s in v["summaries"]:
            fp = s.get("file_path", "")
            file_link = f'<a href="file://{fp}" target="_blank">📄 查看完整</a>' if fp else ""
            brief = s.get("brief", "") or ""
            items += f"""<div class="detail-section">
  <div class="meta">{fmt_cost(s.get('cost',0))} · {s.get('input_tokens',0)} in / {s.get('output_tokens',0)} out · {s.get('created_at','')}</div>
  {f'<div class=\"brief\">{brief[:500]}</div>' if brief else ''}
  <div class="meta" style="margin-top:4px;">{file_link}</div>
</div>"""
        sections += f"""<div class="card"><h3>汇总</h3>{items}</div>"""

    if v.get("transcripts"):
        rows = ""
        for t in v["transcripts"]:
            fp = t.get("file_path", "")
            fl = f'<a href="file://{fp}">📄</a>' if fp else ""
            rows += f"<tr><td>{t.get('model','')}</td><td>{t.get('char_count',0)}</td><td>{fmt_cost(t.get('cost',0))}</td><td>{fl}</td></tr>"
        sections += f"""<div class="card"><h3>转录记录</h3><table><tr><th>模型</th><th>字符数</th><th>费用</th><th>文件</th></tr>{rows}</table></div>"""

    if v.get("visual_analyses"):
        rows = ""
        for a in v["visual_analyses"]:
            fp = a.get("file_path", "")
            fl = f'<a href="file://{fp}">📄</a>' if fp else ""
            rows += f"<tr><td>{a.get('frame_count',0)}</td><td>{fmt_cost(a.get('cost',0))}</td><td>{fl}</td></tr>"
        sections += f"""<div class="card"><h3>视觉分析</h3><table><tr><th>帧数</th><th>费用</th><th>文件</th></tr>{rows}</table></div>"""

    return layout(sections)


@app.route("/search")
def search():
    q = request.args.get("q", "")
    db = DB()
    results = db.search(q) if q else []
    db.close()

    items = ""
    for v in results:
        brief = v.get("brief", "") or ""
        brief_html = f'<div class="brief">{brief[:200]}</div>' if brief else ""
        items += f"""<div class="card">
  <h3><a href="/video/{v['bv']}">{v['title']}</a></h3>
  <div class="meta">{v['bv']} · {v.get('uploader','')}</div>
  {brief_html}
</div>"""
    if not items:
        items = '<div class="card" style="text-align:center;color:#999;padding:40px;">未找到匹配结果</div>'

    return layout(f'<h3 style="margin-bottom:16px;">搜索 "{q}" 共 {len(results)} 条结果</h3>{items}', q=q)


@app.route("/collections")
def collections():
    db = DB()
    cols = db.list_collections()
    db.close()

    cards = ""
    for c in cols:
        cards += f"""<div class="card">
  <h3><a href="/collection/{c['id']}">{c['title']}</a></h3>
  <div class="meta">media_id={c['media_id']} · {c.get('owner_name','')} · {c['recorded_videos']}/{c['video_count']} 视频</div>
</div>"""
    if not cards:
        cards = '<div class="card" style="text-align:center;color:#999;padding:40px;">暂无收藏夹</div>'

    return layout(f'<h3 style="margin-bottom:16px;">收藏夹 ({len(cols)})</h3>{cards}')


@app.route("/collection/<int:cid>")
def collection_detail(cid):
    db = DB()
    c = db.get_collection_detail(cid)
    db.close()
    if not c:
        return layout("<h2>未找到</h2>"), 404

    cards = f"""<div class="card">
  <h2>{c['title']}</h2>
  <div class="meta" style="margin:8px 0;">
    media_id={c['media_id']} · {c.get('owner_name','')} (uid={c.get('owner_uid','')}) · {len(c['videos'])}/{c['video_count']} 视频
  </div>
</div>"""

    for v in c["videos"]:
        total = (v.get("summary_cost", 0) or 0) + (v.get("transcript_cost", 0) or 0) + (v.get("visual_cost", 0) or 0)
        brief = v.get("brief", "") or ""
        brief_html = f'<div class="brief">{brief[:200]}</div>' if brief else ""
        cards += f"""<div class="card">
  <span style="color:#999;font-size:12px;">#{v['order_index']}</span>
  <h3 style="display:inline;"><a href="/video/{v['bv']}">{v['title']}</a></h3>
  <div class="meta">{v['bv']} · {v.get('uploader','')} · {format_duration(v.get('duration_sec'))} · {fmt_cost(total)}</div>
  {brief_html}
</div>"""

    return layout(cards)


@app.route("/stats")
def stats():
    db = DB()
    s = db.stats()
    db.close()
    total = (s["transcript_total_cost"] + s["summary_total_cost"]
             + s["visual_total_cost"] + s["batch_total_cost"])
    items = [
        ("视频总数", s["video_count"]),
        ("总时长", format_duration(s["total_duration_sec"])),
        ("转录数", s["transcript_count"]),
        ("汇总数", s["summary_count"]),
        ("视觉分析", s["visual_count"]),
        ("总费用", f"¥{total:.4f}"),
    ]
    cards = ""
    for label, val in items:
        cards += f'<div class="stat-card"><div class="num">{val}</div><div class="label">{label}</div></div>\n'
    return layout(f'<div class="stat-grid">{cards}</div>')


def main():
    parser = argparse.ArgumentParser(description="Bilibili Pipeline Web UI")
    parser.add_argument("--port", type=int, default=8686)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"[webui] http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
