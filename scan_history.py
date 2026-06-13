#!/usr/bin/env python3
"""
扫描历史处理结果并导入数据库。

扫描路径:
    ~/Downloads/output/*/           — 历史单视频处理目录
    ~/Downloads/batch_*/            — 历史批量处理目录

每个目录下寻找:
    - transcript.txt / .srt         → 转录记录
    - summary.md                    → 汇总记录
    - visual_notes.md / frames/     → 视觉分析
    - 目录名 / 文件名 → 提取 BV 号
"""
import json
import os
import re
import sys
from pathlib import Path

from db import DB


BV_RE = re.compile(r"BV[a-zA-Z0-9]{10,}")
TS_RE = re.compile(r"\d{2}:\d{2}:\d{2}")


def extract_bv(text: str, fallback: str = "") -> str:
    m = BV_RE.search(text)
    if m:
        return m.group(0)
    m = BV_RE.search(fallback)
    return m.group(0) if m else ""


def scan_dir(db: DB, directory: Path, source: str = "import"):
    """Scan a single output directory and record to DB."""
    if not directory.is_dir():
        return

    # Extract BV from directory name
    bv = extract_bv(directory.name)
    url = f"https://www.bilibili.com/video/{bv}" if bv else ""

    title = directory.name
    uploader = ""

    # Look for transcript
    transcript_file = directory / "transcript.txt"
    srt_file = directory / "transcript.srt"
    transcript_cost = 0.0
    char_count = 0
    has_transcript = False

    if transcript_file.exists():
        text = transcript_file.read_text(encoding="utf-8", errors="replace")
        char_count = len(text)
        # Try to find BV in content
        if not bv:
            bv = extract_bv(text)
            url = f"https://www.bilibili.com/video/{bv}" if bv else ""
        # Try to find title in content (first non-empty line)
        for line in text.splitlines():
            line = line.strip()
            if line and len(line) > 5 and not line.startswith("["):
                title = line[:100]
                break
        has_transcript = True

    # Look for summary
    summary_file = directory / "summary.md"
    brief = ""
    summary_cost = 0.0
    in_t = out_t = 0

    if summary_file.exists():
        text = summary_file.read_text(encoding="utf-8", errors="replace")
        # Extract brief (first paragraph)
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and len(line) > 10:
                brief = line[:500]
                break
        if not brief:
            brief = text[:200]
        # Extract cost from content
        m = re.search(r"¥(\d+\.\d+)", text)
        if m:
            summary_cost = float(m.group(1))
        # Get BV from content
        if not bv:
            bv = extract_bv(text)
            url = f"https://www.bilibili.com/video/{bv}" if bv else ""

    # Look for visual analysis
    visual_file = directory / "visual_notes.md"
    frames_dir = directory / "frames"
    visual_cost = 0.0
    frame_count = 0
    has_visual = False

    if visual_file.exists():
        text = visual_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"检测到 (\d+) 个视觉内容", text)
        if m:
            frame_count = int(m.group(1))
        m = re.search(r"¥(\d+\.\d+)", text)
        if m:
            visual_cost = float(m.group(1))
        has_visual = True

    if not frames_dir.exists():
        # Count frame references in visual_notes
        if has_visual:
            text = visual_file.read_text(encoding="utf-8", errors="replace")
            frame_count = len(re.findall(r"!\[帧", text))

    # Only record if we have something
    if not has_transcript and not has_visual and not summary_file.exists():
        return

    if not bv:
        bv = f"unknown_{directory.name[:20]}"

    video_id = db.add_video(
        bv=bv, url=url, title=title, uploader=uploader,
        description="", duration_sec=0, source=source,
    )

    if has_transcript and transcript_file.exists():
        db.add_transcript(
            video_id=video_id,
            file_path=str(transcript_file),
            model="whisper",
            char_count=char_count,
            cost=transcript_cost,
        )

    if summary_file.exists():
        db.add_summary(
            video_id=video_id,
            file_path=str(summary_file),
            brief=brief,
            input_tokens=in_t,
            output_tokens=out_t,
            cost=summary_cost,
            model="import",
        )

    if has_visual:
        db.add_visual_analysis(
            video_id=video_id,
            file_path=str(visual_file),
            frame_count=frame_count,
            cost=visual_cost,
            model="import",
        )

    print(f"  ✓ {bv}: {title[:40]:40s} {'T' if has_transcript else ' '}{'S' if summary_file.exists() else ' '}{'V' if has_visual else ' '}")


def scan_history():
    db = DB()

    # Scan ~/Documents/bilibili/clips/
    clips_dir = Path.home() / "Documents" / "bilibili" / "clips"
    if clips_dir.exists():
        print(f"扫描 {clips_dir}...")
        for d in sorted(clips_dir.iterdir()):
            if d.is_dir():
                scan_dir(db, d, source="manual")

    # Scan ~/Documents/bilibili/batch/*/
    batch_dir = Path.home() / "Documents" / "bilibili" / "batch"
    if batch_dir.exists():
        for batch in sorted(batch_dir.iterdir()):
            if not batch.is_dir():
                continue
            print(f"\n扫描批量目录 {batch.name}...")
            for sub in sorted(batch.iterdir()):
                if sub.is_dir():
                    scan_dir(db, sub, source="batch")

    print(f"\n完成。数据库: {db.path}")


if __name__ == "__main__":
    scan_history()
