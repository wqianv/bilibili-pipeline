#!/usr/bin/env python3
"""
逐条处理收藏夹视频: 下载 → 转写 → 汇总, 带大随机间隔, 防 Ban。

用法:
    python3 process_fav.py                              # 处理 fav_urls.txt 中未完成的视频
    python3 process_fav.py --urls-file fav_urls.txt
    python3 process_fav.py --sleep-interval 60 180       # 自定义间隔 (秒)
    python3 process_fav.py --no-visual                   # 跳过视觉分析
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from db import DB

BASE = Path(__file__).parent


def load_config():
    return json.loads((BASE / "config.json").read_text())


def get_random_sleep(min_s, max_s):
    return random.randint(min_s, max_s)


def get_output_dir(bv: str) -> Path:
    out_base = Path.home() / "Documents" / "bilibili" / "clips"
    out_base.mkdir(parents=True, exist_ok=True)
    return out_base / f"{bv}_{time.strftime('%Y%m%d')}"


def main():
    parser = argparse.ArgumentParser(description="逐条处理收藏夹视频")
    parser.add_argument("--urls-file", default=str(Path.home() / "Documents" / "bilibili" / "fav_urls.txt"),
                        help="URL 列表文件")
    parser.add_argument("--sleep-interval", nargs=2, type=int, default=[30, 120],
                        help="随机间隔范围(秒)，默认 30 120")
    parser.add_argument("--transcribe-only", action="store_true",
                        help="只转写不汇总")
    parser.add_argument("--skip-download", action="store_true",
                        help="跳过下载（如果视频已存在）")
    parser.add_argument("--max-videos", type=int, default=0,
                        help="最多处理 N 个（默认全部）")
    parser.add_argument("--config", type=str, default="",
                        help="传递给 pipeline 子命令的额外参数")
    args = parser.parse_args()

    urls_file = Path(args.urls_file).expanduser()
    if not urls_file.exists():
        print(f"文件不存在: {urls_file}")
        return

    urls = [l.strip() for l in urls_file.read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    if not urls:
        print("URL 列表为空")
        return

    config = load_config()
    cfg = config.get("download", {})
    headers = cfg.get("headers", [])
    fmt = cfg.get("format", "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst")

    min_sleep, max_sleep = args.sleep_interval
    db = DB()

    processed = 0
    skipped = 0
    for i, url in enumerate(urls, 1):
        # Extract BV
        bv = ""
        if "video/" in url:
            bv = url.split("video/")[-1].split("?")[0].split("/")[0]
        if not bv:
            print(f"[{i}/{len(urls)}] 无法解析 BV: {url}")
            continue

        print(f"\n{'='*60}")
        print(f"[{i}/{len(urls)}] BV: {bv}")
        print(f"{'='*60}")

        # Check if already done
        vid = db.get_video_by_bv(bv)
        if vid and vid.get("t_status") == "done" and vid.get("s_status") == "done":
            print(f"  已处理完毕，跳过")
            skipped += 1
            continue

        if args.max_videos and processed >= args.max_videos:
            print(f"  已达上限 {args.max_videos}，停止")
            break

        out_dir = get_output_dir(bv)
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 1: Download ──
        video_path = out_dir / "video.mp4"
        audio_path = out_dir / "audio.mp3"
        if not video_path.exists() and not args.skip_download:
            print(f"  [下载] 开始...")
            header_args = []
            for h in headers:
                if ":" in h:
                    header_args.extend(["--add-header", h])

            cmd = [
                "yt-dlp",
                "--quiet", "--no-warnings",
                "--no-cookies-from-browser",
                "-f", fmt,
                "--limit-rate", "5M",
                "--retries", "5",
                "--file-access-retries", "3",
                "--fragment-retries", "3",
                "-o", str(video_path),
                *header_args,
                url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  [下载] 失败: {result.stderr[:200]}")
                sleep_s = get_random_sleep(min_sleep, max_sleep)
                print(f"  等待 {sleep_s}s 后继续...")
                time.sleep(sleep_s)
                continue
            print(f"  [下载] ✓")
        else:
            print(f"  [下载] 已存在，跳过")

        # ── Step 2: Transcribe ──
        t_done = vid and vid.get("t_status") == "done"
        if not t_done:
            # Extract audio if needed
            if not audio_path.exists() and video_path.exists():
                print(f"  [音频] 提取中...")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(video_path),
                     "-vn", "-acodec", "libmp3lame",
                     "-q:a", "2", str(audio_path)],
                    capture_output=True,
                )
                print(f"  [音频] ✓")

            print(f"  [转写] 开始...")
            transcribe_cmd = [
                sys.executable, str(BASE / "transcribe.py"),
                "--audio", str(audio_path),
                "--outdir", str(out_dir),
                "--bv", bv,
            ]
            result = subprocess.run(transcribe_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  [转写] 失败: {result.stderr[:200]}")
                continue
            for line in result.stdout.splitlines():
                if "[cost]" in line:
                    print(f"  {line.strip()}")
            # Record to DB (transcribe.py doesn't do this itself)
            transcript_file = out_dir / "transcript.txt"
            char_count = len(transcript_file.read_text(encoding="utf-8", errors="replace")) if transcript_file.exists() else 0
            vid = db.get_video_by_bv(bv)
            if vid:
                db.add_transcript(
                    video_id=vid["id"],
                    file_path=str(transcript_file) if transcript_file.exists() else "",
                    model="whisper",
                    char_count=char_count,
                )
            # Re-fetch vid to get updated status for next steps
            vid = db.get_video_by_bv(bv)
            print(f"  [转写] ✓")
        else:
            print(f"  [转写] 已完成，跳过")

        # ── Step 3: Summarize ──
        if not args.transcribe_only:
            s_done = vid and vid.get("s_status") == "done"
            if s_done:
                print(f"  [汇总] 已完成，跳过")
            else:
                transcript_path = out_dir / "transcript.txt"
                if not transcript_path.exists():
                    print(f"  [汇总] transcript.txt 不存在，跳过")
                else:
                    print(f"  [汇总] 开始...")
                    summarize_cmd = [
                        sys.executable, str(BASE / "summarize.py"),
                        "--transcript", str(transcript_path),
                        "--outdir", str(out_dir),
                        "--bv", bv,
                    ]
                    if args.config:
                        summarize_cmd.extend(args.config.split())
                    result = subprocess.run(summarize_cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"  [汇总] 失败: {result.stderr[:200]}")
                        continue
                    for line in result.stdout.splitlines():
                        if "[cost]" in line:
                            print(f"  {line.strip()}")
                    print(f"  [汇总] ✓")

        processed += 1

        # ── Sleep between videos ──
        if i < len(urls):
            sleep_s = get_random_sleep(min_sleep, max_sleep)
            print(f"\n  等待 {sleep_s}s 后处理下一个...")
            time.sleep(sleep_s)

    db.close()
    print(f"\n{'='*60}")
    print(f"完成! 处理 {processed} 个, 跳过 {skipped} 个 (共 {len(urls)})")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
