#!/usr/bin/env python3
"""
batch_up.py — 批量采集指定 UP 主的所有视频，逐一转录+总结，最后汇总报告。

用法:
  python3 batch_up.py --up-uid 385474 --cookies ./bilibili_cookies.txt
  python3 batch_up.py --up-uid 385474 --cookies ./cookies.txt --max-videos 5
  python3 batch_up.py --urls-file ./urls.txt
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

WHISPER_MODEL = "base"
SCRIPT_DIR = Path(__file__).parent


def load_config():
    with open(SCRIPT_DIR / "config.json") as f:
        return json.load(f)


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


def read_api_key(key_name: str) -> Optional[str]:
    key = os.environ.get(key_name)
    if key:
        return key
    env_path = SCRIPT_DIR / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key_name}="):
                    return line.split("=", 1)[1].strip("\"'")
    return None


def build_ytdlp_headers(config) -> list:
    headers = []
    for h in config["download"]["headers"]:
        headers += ["--add-header", h]
    return headers


def parse_args():
    p = argparse.ArgumentParser(
        description="批量采集 UP 主所有视频并汇总",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_argument_group("输入来源（二选一，优先 --up-uid）")
    src.add_argument("--up-uid", help="Bilibili UP 主 UID（数字）")
    src.add_argument("--urls-file", type=Path, help="视频 URL 列表文件（每行一个）")
    p.add_argument("--cookies", type=Path, help="Bilibili cookies.txt 路径（空间列表必需）")
    p.add_argument("--max-videos", type=int, default=0,
                    help="最多处理前 N 个视频（0=全部）")
    p.add_argument("--output-dir", type=Path, default=None,
                    help="输出根目录（默认 auto/batch_<UID>_<date>）")
    p.add_argument("--resume", action="store_true",
                    help="续传模式：跳过已有输出的视频")
    p.add_argument("--sleep-interval", type=int, nargs=2, default=[5, 15],
                    metavar=("MIN", "MAX"),
                    help="视频间随机休眠范围（默认 5 15s）")
    p.add_argument("--limit-rate", default="5M",
                    help="下载速度上限（默认 5M）")
    p.add_argument("--retries", type=int, default=3,
                    help="重试次数（默认 3）")
    return p.parse_args()


def resolve_up_info(uid: str, cookies: Optional[Path], config) -> dict:
    url = f"https://space.bilibili.com/{uid}/video"
    cmd = ["yt-dlp", "--flat-playlist", "--dump-json",
           "--no-warnings", "--no-cookies-from-browser"]
    cmd += build_ytdlp_headers(config)
    if cookies:
        cmd += ["--cookies", str(cookies)]
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"获取 UP 主视频列表失败。\n"
            f"  Bilibili 空间列表需要登录态 cookies。\n"
            f"  解决方法：\n"
            f"    1. 在浏览器登录 Bilibili\n"
            f"    2. 安装 Export Cookies 扩展导出 cookies.txt\n"
            f"    3. 加上 --cookies ./cookies.txt 重试\n"
            f"  或者手动提供 URL 列表：\n"
            f"    python3 batch_up.py --urls-file ./urls.txt\n"
            f"\n原始错误: {result.stderr.strip()[:200]}"
        )

    name = uid
    videos = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        try:
            info = json.loads(line)
            if "uploader" in info and info["uploader"] and name == uid:
                name = info["uploader"]
            videos.append(info)
        except json.JSONDecodeError:
            continue
    return {"uid": uid, "name": name, "videos": videos}


def load_urls_file(path: Path) -> list[str]:
    if not path.exists():
        print(f"[错误] 文件不存在: {path}")
        sys.exit(1)
    with open(path) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def extract_bv(text: str) -> str:
    m = re.search(r"BV[a-zA-Z0-9]{10,}", text)
    return m.group(0) if m else ""


def video_out_dir(base: Path, index: int, title: str, bv: str = "") -> Path:
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
    label = f"{bv}_" if bv else ""
    return base / f"{index:03d}-{label}{safe}"


def process_single_video(url: str, outdir: Path, args, config, bv: str = "") -> Optional[dict]:
    if outdir.exists() and args.resume:
        summary_file = outdir / f"{bv}_summary.md" if bv else outdir / "summary.md"
        if summary_file.exists() and summary_file.stat().st_size > 0:
            print(f"  ↻ 已有输出，跳过")
            return None

    prefix = f"{bv}_" if bv else ""
    outdir.mkdir(parents=True, exist_ok=True)
    dl_headers = build_ytdlp_headers(config)

    # Phase 1: download audio
    audio_path = outdir / f"{prefix}audio.mp3"
    if not audio_path.exists():
        print(f"  [1/3] 下载音频...")
        cmd = ["yt-dlp", "-x", "--audio-format", "mp3",
               "--limit-rate", args.limit_rate,
               "--retries", str(args.retries),
               "--file-access-retries", str(args.retries),
               "--no-cookies-from-browser"] + dl_headers + [
               "-o", str(outdir / f"{prefix}audio.%(ext)s"), url]
        if args.cookies:
            cmd.insert(-1, "--cookies")
            cmd.insert(-1, str(args.cookies))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ✗ 下载失败: {r.stderr.strip()[:100]}")
            return {"url": url, "status": "download_failed", "error": r.stderr.strip()[:200]}

    # Phase 2: transcribe
    srt_path = outdir / f"{prefix}transcript.srt"
    if not srt_path.exists():
        print(f"  [2/3] 语音转文字...")
        bv_flag = ["--bv", bv] if bv else []
        r = subprocess.run(
            ["python3", str(SCRIPT_DIR / "transcribe.py"),
             "--audio", str(audio_path),
             "--outdir", str(outdir),
             "--model", WHISPER_MODEL] + bv_flag,
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            print(f"  ✗ 转写失败: {r.stderr.strip()[:200]}")
            return {"url": url, "status": "transcribe_failed", "error": r.stderr.strip()[:200]}

    # Rename transcript files to BV-prefixed
    for fname in ["transcript.txt", "transcript.srt"]:
        src = outdir / fname
        dst = outdir / f"{prefix}{fname}"
        if src.exists() and src != dst:
            src.rename(dst)

    # Phase 3: summarize
    summary_path = outdir / f"{prefix}summary.md"
    transcript_path = outdir / f"{prefix}transcript.txt"
    summarize_cost = 0.0
    if not summary_path.exists() and transcript_path.exists():
        print(f"  [3/3] LLM 总结...")
        bv_flag = ["--bv", bv] if bv else []
        r = subprocess.run(
            ["python3", str(SCRIPT_DIR / "summarize.py"),
             "--transcript", str(transcript_path),
             "--outdir", str(outdir)] + bv_flag,
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            print(f"  ✗ 总结失败")
            return {"url": url, "status": "summarize_failed"}
        for line in r.stdout.splitlines():
            m = re.search(r"¥(\d+\.\d+)", line)
            if m:
                summarize_cost = float(m.group(1))
                break

    cost = summarize_cost

    # Record video to database
    if bv:
        try:
            import subprocess as _sp
            _sp.run(["python3", str(SCRIPT_DIR / "db.py"), "record", "video",
                     json.dumps({"bv": bv, "url": url, "title": outdir.name})],
                    capture_output=True, timeout=10)
        except Exception:
            pass

    return {"url": url, "status": "ok", "cost_cny": cost, "bv": bv}


def load_summaries(base_dir: Path, video_dirs: list[tuple[int, str, Path]]) -> list[dict]:
    summaries = []
    for idx, title, vdir in video_dirs:
        summary_file = vdir / "summary.md"
        if summary_file.exists():
            text = summary_file.read_text(encoding="utf-8")
            summaries.append({"index": idx, "title": title, "summary": text})
    return summaries


def create_master_summary(up_name: str, summaries: list[dict], api_key: str,
                          sum_cfg: dict, provider: dict, prompt_text: str) -> str:
    from openai import OpenAI

    blocks = []
    for s in summaries:
        blocks.append(f"--- 视频 {s['index']}: {s['title']} ---\n{s['summary']}")
    text = "\n\n".join(blocks)
    print(f"\n[汇总] 正在生成全局汇总报告 ({len(summaries)} 个视频)...")

    client = OpenAI(api_key=api_key, base_url=provider["base_url"])
    resp = client.chat.completions.create(
        model=sum_cfg["model"],
        messages=[
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": f"以下是一个 UP 主「{up_name}」的 {len(summaries)} 个视频的分别总结，请生成全局汇总报告：\n\n{text}"},
        ],
        temperature=sum_cfg["temperature"],
        max_tokens=sum_cfg["max_tokens"],
    )

    in_t = resp.usage.prompt_tokens
    out_t = resp.usage.completion_tokens
    pi = sum_cfg["price_input"]
    po = sum_cfg["price_output"]
    cost = (in_t * pi + out_t * po) / 1_000_000
    print(f"[cost] 全局汇总（{sum_cfg['model']}）: ¥{cost:.4f}（输入 {in_t} + 输出 {out_t} tokens）")

    return resp.choices[0].message.content


def main():
    args = parse_args()
    config = load_config()
    sum_cfg = resolve_step(config, config["summarize"])
    summarize_provider = config["providers"][sum_cfg["provider"]]
    master_prompt = config["prompts"]["master_summarize"]

    ds_key = read_api_key(summarize_provider["api_key_env"])
    if not ds_key:
        print(f"[错误] 请设置 {summarize_provider['api_key_env']}（.env 或环境变量）")
        sys.exit(1)

    # 获取视频列表
    if args.up_uid:
        try:
            info = resolve_up_info(args.up_uid, args.cookies, config)
        except RuntimeError as e:
            print(f"[错误] {e}")
            sys.exit(1)
        videos = info["videos"]
        up_name = info["name"]
    elif args.urls_file:
        urls = load_urls_file(args.urls_file)
        videos = [{"url": u, "title": Path(u).stem} for u in urls]
        up_name = args.urls_file.stem
    else:
        print("[错误] 请指定 --up-uid 或 --urls-file")
        sys.exit(1)

    if not videos:
        print("[错误] 没有找到任何视频")
        sys.exit(1)

    if args.max_videos > 0:
        videos = videos[:args.max_videos]

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_dir:
        base_dir = args.output_dir
    else:
        # Read output base from config
        base = SCRIPT_DIR / "config.json"
        output_base = "~/Documents/bilibili"
        with open(base) as f:
            cfg = json.load(f)
            output_base = cfg.get("output_dir", output_base)
        output_base = Path(output_base).expanduser()
        base_dir = output_base / "batch" / f"{up_name}_{date_str}"
    base_dir.mkdir(parents=True, exist_ok=True)

    total = len(videos)
    results = []
    video_dirs = []

    print(f"\n{'='*60}")
    print(f"UP 主: {up_name}  ({args.up_uid or '手动列表'})")
    print(f"视频数: {total}")
    print(f"输出: {base_dir}")
    print(f"{'='*60}\n")

    total_cost = 0.0
    success = 0
    failed = 0

    for i, v in enumerate(videos, 1):
        url = v.get("url") or v.get("webpage_url", "")
        title = v.get("title", f"video_{i}")
        bv = extract_bv(url) or extract_bv(title)
        print(f"\n[{i}/{total}] {title}")
        print(f"  URL: {url}")

        vdir = video_out_dir(base_dir, i, title, bv)
        video_dirs.append((i, title, vdir))
        result = process_single_video(url, vdir, args, config, bv)

        if result and result.get("status") == "ok":
            success += 1
            tc = result.get("cost_cny", 0)
            total_cost += tc
            print(f"  ✓ 完成  (¥{tc:.4f})")
        elif result:
            failed += 1
            print(f"  ✗ {result.get('status', '未知错误')}")
        else:
            pass

        if i < total:
            delay = random.uniform(args.sleep_interval[0], args.sleep_interval[1])
            print(f"  [等待 {delay:.0f}s 后处理下一个]")
            time.sleep(delay)

    print(f"\n{'='*60}")
    print(f"[汇总] 处理完成: {success}/{total} 成功 ({failed} 失败)")

    summaries_data = load_summaries(base_dir, video_dirs)
    if summaries_data:
        master = create_master_summary(up_name, summaries_data, ds_key,
                                       sum_cfg, summarize_provider, master_prompt)
        master_path = base_dir / "master_summary.md"
        master_path.write_text(master, encoding="utf-8")
        print(f"[汇总] 全局报告: {master_path}")
    else:
        print("[汇总] 没有可用的总结，跳过全局报告")

    print(f"\n{'='*60}")
    print(f"费用汇总")
    print(f"{'='*60}")
    print(f"  单视频总结: ¥{total_cost:.4f}")
    print(f"  总费用:     ¥{total_cost:.4f}")
    print(f"{'='*60}")

    cost_log = {
        "up_name": up_name,
        "up_uid": args.up_uid,
        "total_videos": total,
        "success": success,
        "failed": failed,
        "total_cost_cny": round(total_cost, 6),
        "pricing": {
            "model": sum_cfg["model"],
            "input": f"¥{sum_cfg['price_input']}/1M tok",
            "output": f"¥{sum_cfg['price_output']}/1M tok",
        },
    }
    cost_path = base_dir / "cost_log.json"
    with open(cost_path, "w") as f:
        json.dump(cost_log, f, ensure_ascii=False, indent=2)
    print(f"\n费用日志: {cost_path}")

    # Record batch job to database
    try:
        bv_list = [r.get("bv", "") for r in results if r]
        subprocess.run(
            ["python3", str(SCRIPT_DIR / "db.py"), "record", "batch",
             json.dumps({"name": up_name, "type": "up",
                        "video_ids": bv_list, "total_cost": round(total_cost, 6),
                        "output_dir": str(base_dir)})],
            capture_output=True, timeout=10)
    except Exception:
        pass

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
