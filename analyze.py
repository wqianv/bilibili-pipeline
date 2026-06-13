#!/usr/bin/env python3
"""
analyze.py — Multimodal visual analysis for Bilibili videos.
All model/config in config.json — no hardcoded values.

Usage:
    python3 analyze.py --video-url <url> --outdir output/ [--pipeline <name>] [--force]
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from openai import OpenAI

TS_LINE_RE = re.compile(r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})")


def resolve_step(config, step_cfg):
    m = step_cfg["model"]
    mdef = config["model_defs"][m]
    return {
        "provider": mdef["provider"],
        "model": mdef["model"],
        "price_input": mdef["price_input"],
        "price_output": mdef["price_output"],
        "temperature": step_cfg.get("temperature"),
        "max_tokens": step_cfg.get("max_tokens"),
    }


def load_config():
    cfg_path = Path(__file__).parent / "config.json"
    with open(cfg_path) as f:
        return json.load(f)


def read_api_key(env_dir, key_name):
    key = os.environ.get(key_name)
    if key:
        return key
    env_path = Path(env_dir) / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key_name}="):
                    return line.split("=", 1)[1].strip("\"'")
    return None


def build_client(config, provider_name):
    provider = config["providers"][provider_name]
    api_key = read_api_key(Path(__file__).parent, provider["api_key_env"])
    if not api_key:
        print(f"Error: {provider['api_key_env']} not found for provider '{provider_name}'")
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=provider["base_url"])


def parse_srt(srt_path):
    lines = []
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = content.strip().split("\n\n")
    for block in blocks:
        parts = block.split("\n")
        if len(parts) < 3:
            continue
        time_line = parts[1]
        match = TS_LINE_RE.match(time_line)
        if not match:
            continue
        start_ts = match.group(1).split(",")[0]
        text = " ".join(p[2:] for p in parts[2:])
        lines.append((start_ts, text))
    return lines


def detect_visual_cues(client, model, cue_prompt, srt_path, temperature, max_tokens):
    lines = parse_srt(srt_path)
    if not lines:
        return [], None
    ts_lines = [f"[{ts}] {text}" for ts, text in lines]
    text = "\n".join(ts_lines)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": cue_prompt},
            {"role": "user", "content": f"分析以下逐字稿，找出提及视觉内容的时间点：\n\n{text}"},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    result = response.choices[0].message.content.strip()
    if result == "NONE":
        return [], response.usage
    timestamps = []
    for line in result.split("\n"):
        line = line.strip().rstrip(".")
        match = re.match(r"(\d{2}:\d{2}:\d{2})", line)
        if match:
            timestamps.append(match.group(1))
    return timestamps, response.usage


def dedup_timestamps(timestamps, min_gap=2):
    if not timestamps:
        return []
    kept = [timestamps[0]]
    for ts in timestamps[1:]:
        parts = ts.split(":")
        sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        last_parts = kept[-1].split(":")
        last_sec = int(last_parts[0]) * 3600 + int(last_parts[1]) * 60 + float(last_parts[2])
        if sec - last_sec >= min_gap:
            kept.append(ts)
    return kept


def download_video(url, out_path, headers, fmt):
    cmd = ["yt-dlp"]
    for h in headers:
        cmd += ["--add-header", h]
    cmd += ["-f", fmt, "-o", out_path, "--no-playlist", url]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path


def extract_frame(video_path, timestamp, out_path):
    cmd = ["ffmpeg", "-ss", timestamp, "-i", video_path, "-vframes", "1", "-q:v", "2", "-y", out_path]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def describe_frame(client, model, frame_path, prompt_text, temperature, max_tokens):
    with open(frame_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content, response.usage


def main():
    config = load_config()
    pipeline_names = list(config["pipelines"].keys())

    parser = argparse.ArgumentParser(description="Multimodal visual analysis")
    parser.add_argument("--video-url", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--pipeline", default="agnes_full", choices=pipeline_names,
                        help="Pipeline config from config.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bv", default="", help="BV prefix for filenames")
    args = parser.parse_args()

    prefix = f"{args.bv}_" if args.bv else ""
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # Look for srt with or without BV prefix
    srt_path = outdir / f"{prefix}transcript.srt"
    if not srt_path.exists():
        srt_path = outdir / "transcript.srt"
    note_path = outdir / f"{prefix}visual_notes.md"

    if not srt_path.exists():
        print(f"Error: {srt_path} not found. Run pipeline.sh first.")
        sys.exit(1)

    if note_path.exists() and not args.force:
        print(f"[analyze] Cached: {note_path} already exists (use --force to re-run)")
        return

    pipeline = config["pipelines"][args.pipeline]
    dl_cfg = config["download"]
    prompts = config["prompts"]

    cue_cfg = resolve_step(config, pipeline["visual_cue"])
    frame_cfg = resolve_step(config, pipeline["frame_analysis"])

    cue_client = build_client(config, cue_cfg["provider"])
    frame_client = build_client(config, frame_cfg["provider"])

    # Phase 1: detect visual cues with optional fallback
    print(f"[analyze] Scanning transcript for visual cues ({cue_cfg['model']})...")
    timestamps, cue_usage = detect_visual_cues(
        cue_client, cue_cfg["model"], prompts["visual_cue"],
        str(srt_path), cue_cfg["temperature"], cue_cfg["max_tokens"],
    )
    cue_model_used = cue_cfg["model"]
    frame_model_used = frame_cfg["model"]

    if not timestamps and pipeline.get("fallback"):
        fb_name = pipeline["fallback"]
        fb_cfg = config["pipelines"][fb_name]
        fb_cue_cfg = resolve_step(config, fb_cfg["visual_cue"])
        fb_frame_cfg = resolve_step(config, fb_cfg["frame_analysis"])
        print(f"[analyze] 主流程未检测到视觉线索，切换到兜底流程 ({fb_cue_cfg['model']})...")
        fb_client = build_client(config, fb_cue_cfg["provider"])
        fb_ts, fb_usage = detect_visual_cues(
            fb_client, fb_cue_cfg["model"], prompts["visual_cue"],
            str(srt_path), fb_cue_cfg["temperature"], fb_cue_cfg["max_tokens"],
        )
        if fb_ts:
            timestamps = fb_ts
            cue_usage = fb_usage
            cue_cfg = fb_cue_cfg
            frame_cfg = fb_frame_cfg
            frame_client = build_client(config, frame_cfg["provider"])
            cue_model_used = fb_cue_cfg["model"]
            frame_model_used = fb_frame_cfg["model"]
            print(f"[analyze] 兜底流程找到 {len(timestamps)} 个视觉线索")
        else:
            print("[analyze] 兜底流程也未找到视觉线索")

    cue_cost = 0
    if cue_usage:
        in_t = cue_usage.prompt_tokens
        out_t = cue_usage.completion_tokens
        cue_cost = (in_t * cue_cfg["price_input"] + out_t * cue_cfg["price_output"]) / 1_000_000
        print(f"[cost]   线索检测（{cue_model_used}）: ¥{cue_cost:.4f}（输入 {in_t} + 输出 {out_t} tokens）")

    before = len(timestamps)
    timestamps = dedup_timestamps(timestamps)
    if len(timestamps) < before:
        print(f"[analyze] 去重合并: {before} → {len(timestamps)} 帧")

    if not timestamps:
        print("[analyze] No visual cues found")
        with open(note_path, "w") as f:
            f.write("# 视觉分析\n\n未检测到需要分析的视觉内容。\n")
        print(f"[analyze] Report saved to: {note_path}")
        print(f"[cost] 总视觉分析费用: ¥{cue_cost:.4f}")
        return

    print(f"[analyze] Found {len(timestamps)} visual cue timestamps:")
    for ts in timestamps:
        print(f"  - {ts}")

    # Phase 2: download video (cached)
    video_path = outdir / f"{prefix}video.mp4"
    if not video_path.exists() or args.force:
        print("[analyze] Downloading video (low quality)...")
        download_video(args.video_url, str(video_path), dl_cfg["headers"], dl_cfg["format"])
        print(f"[analyze] Video saved to: {video_path}")
    else:
        print(f"[analyze] Using cached video: {video_path}")

    # Phase 3: extract frames + describe (cached per frame)
    frames_dir = outdir / f"{prefix}frames"
    frames_dir.mkdir(exist_ok=True)

    total_in = 0
    total_out = 0
    descriptions = []
    frame_prompt = prompts["frame_analysis"]

    for i, ts in enumerate(timestamps):
        frame_name = f"frame_{i:02d}_{ts.replace(':', '-')}.jpg"
        frame_path = frames_dir / frame_name

        if not frame_path.exists() or args.force:
            print(f"[analyze] Extracting frame at {ts}...")
            extract_frame(str(video_path), ts, str(frame_path))

        print(f"[analyze] Analyzing frame at {ts}...")
        desc, usage = describe_frame(frame_client, frame_cfg["model"], str(frame_path),
                                     frame_prompt, frame_cfg["temperature"], frame_cfg["max_tokens"])
        total_in += usage.prompt_tokens
        total_out += usage.completion_tokens
        descriptions.append((ts, frame_name, desc))
        time.sleep(0.5)

    frame_cost = (total_in * frame_cfg["price_input"] + total_out * frame_cfg["price_output"]) / 1_000_000
    print(f"[cost]   帧分析（{frame_model_used} × {len(timestamps)} 帧）: ¥{frame_cost:.4f}（输入 {total_in} + 输出 {total_out} tokens）")
    total = cue_cost + frame_cost
    print(f"[cost] 总视觉分析费用: ¥{total:.4f}")

    with open(note_path, "w", encoding="utf-8") as f:
        f.write("# 视觉分析\n\n")
        f.write(f"- 线索模型: {cue_model_used}\n")
        f.write(f"- 帧分析模型: {frame_model_used}\n")
        f.write(f"- 检测到 {len(descriptions)} 个视觉内容片段\n\n")
        for i, (ts, fname, desc) in enumerate(descriptions):
            f.write(f"## 片段 {i+1} — {ts}\n\n")
            f.write(f"![帧 {ts}]({prefix}frames/{fname})\n\n")
            f.write(f"{desc}\n\n")
            f.write("---\n\n")

    print(f"[analyze] Visual analysis saved to: {note_path}")

    # Record to database
    if args.bv:
        try:
            from db import DB
            db = DB()
            db.upsert_video(bv=args.bv, url=args.video_url)
            db.add_visual_analysis(
                video_id=db.get_video_by_bv(args.bv)["id"],
                file_path=str(note_path),
                frame_count=len(descriptions),
                cost=frame_cost + cue_cost,
                model=frame_model_used,
            )
            db.close()
        except Exception as e:
            print(f"[db] Warning: {e}")


if __name__ == "__main__":
    main()
