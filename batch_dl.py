#!/usr/bin/env python3
"""
batch_dl.py — 健壮的批量视频下载器（带防封禁策略）

所有防封禁参数均可通过 CLI 参数或环境变量覆盖。
环境变量优先级最高，适用于 Docker / CI 等自动化场景。

用法:
  python3 batch_dl.py --url "https://www.bilibili.com/video/BV1xx411c7mD"
  python3 batch_dl.py --urls-file ./urls.txt
  python3 batch_dl.py --playlist "https://www.youtube.com/playlist?list=..."
  python3 batch_dl.py --url "https://..." --cookies ./cookies.txt --output-dir ./downloads

防封禁参数一览:
  --sleep-requests N     每次 API 请求前强制休眠 N 秒（默认 3）
  --sleep-interval M N   下载间随机休眠 M~N 秒（默认 5 15）
  --limit-rate STR       下载速度硬上限（默认 5M，支持 K/M/G）
  --retries N            网络/403 错误最大重试次数（默认 3）
  --cookies PATH         白板小号 cookies.txt，禁用自动扫描
  --output-dir PATH      下载根目录（默认 ./downloads）
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path


# ── 默认值（环境变量可覆盖） ──────────────────────────────────────────────
ENV_PREFIX = "BATCH_DL_"
SCRIPT_DIR = Path(__file__).parent


def load_headers():
    with open(SCRIPT_DIR / "config.json") as f:
        cfg = json.load(f)
    headers = []
    for h in cfg["download"]["headers"]:
        headers += ["--add-header", h]
    return headers


def get_default_output_dir() -> str:
    try:
        with open(SCRIPT_DIR / "config.json") as f:
            cfg = json.load(f)
            return cfg.get("output_dir", "~/Documents/bilibili") + "/downloads"
    except Exception:
        return "~/Documents/bilibili/downloads"


DEFAULTS = {
    "sleep_requests": int(os.environ.get(f"{ENV_PREFIX}SLEEP_REQUESTS", 3)),
    "sleep_interval_min": int(os.environ.get(f"{ENV_PREFIX}SLEEP_INTERVAL_MIN", 5)),
    "sleep_interval_max": int(os.environ.get(f"{ENV_PREFIX}SLEEP_INTERVAL_MAX", 15)),
    "limit_rate": os.environ.get(f"{ENV_PREFIX}LIMIT_RATE", "5M"),
    "retries": int(os.environ.get(f"{ENV_PREFIX}RETRIES", 3)),
    "output_dir": os.environ.get(f"{ENV_PREFIX}OUTPUT_DIR", get_default_output_dir()),
}

OUTPUT_TEMPLATE = (
    "%(playlist_title,uploader,channel,Unknown)s/"
    "%(upload_date>%Y-%m-%d,unknown)s-"
    "%(title)s.%(ext)s"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="健壮的批量视频下载器（带防封禁策略）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
环境变量（优先级最高）:
  BATCH_DL_SLEEP_REQUESTS      每次 API 请求前休眠秒数
  BATCH_DL_SLEEP_INTERVAL_MIN  下载间随机休眠下限
  BATCH_DL_SLEEP_INTERVAL_MAX  下载间随机休眠上限
  BATCH_DL_LIMIT_RATE          下载速度上限 (如 5M, 10M, 1G)
  BATCH_DL_RETRIES             最大重试次数
  BATCH_DL_OUTPUT_DIR          下载根目录

示例:
  BATCH_DL_LIMIT_RATE=10M python3 batch_dl.py --url "https://..."
  BATCH_DL_COOKIES=./cookies.txt BATCH_DL_RETRIES=5 python3 batch_dl.py --urls-file ./list.txt
        """,
    )

    src = p.add_argument_group("输入来源（三选一）")
    src.add_argument("--url", help="单个视频 URL")
    src.add_argument("--urls-file", type=Path, help="URL 列表文件（每行一个）")
    src.add_argument("--playlist", help="播放列表/频道 URL（自动展开）")

    ab = p.add_argument_group("防封禁参数")
    ab.add_argument(
        "--sleep-requests", type=int, default=DEFAULTS["sleep_requests"],
        metavar="SEC",
        help=f"API 请求前强制休眠（默认 {DEFAULTS['sleep_requests']}s）",
    )
    ab.add_argument(
        "--sleep-interval", type=int, nargs=2,
        default=[DEFAULTS["sleep_interval_min"], DEFAULTS["sleep_interval_max"]],
        metavar=("MIN", "MAX"),
        help=f"下载间随机休眠范围（默认 {DEFAULTS['sleep_interval_min']} {DEFAULTS['sleep_interval_max']}s）",
    )
    ab.add_argument(
        "--limit-rate", default=DEFAULTS["limit_rate"],
        help=f"下载速度上限（默认 {DEFAULTS['limit_rate']}，支持 K/M/G）",
    )
    ab.add_argument(
        "--retries", type=int, default=DEFAULTS["retries"],
        help=f"网络/403 错误最大重试次数（默认 {DEFAULTS['retries']}）",
    )

    io = p.add_argument_group("输入输出")
    io.add_argument(
        "--cookies", type=Path,
        help="cookies.txt 路径（禁用浏览器自动扫描）",
    )
    io.add_argument(
        "--output-dir", type=Path, default=Path(DEFAULTS["output_dir"]),
        help=f"下载根目录（默认 {DEFAULTS['output_dir']}）",
    )
    io.add_argument(
        "--output-template",
        default=os.environ.get(f"{ENV_PREFIX}OUTPUT_TEMPLATE", OUTPUT_TEMPLATE),
        help="yt-dlp 输出模板（默认见源码 OUTPUT_TEMPLATE）",
    )
    io.add_argument(
        "--metadata", action="store_true",
        help="同时下载缩略图和描述文件",
    )
    io.add_argument(
        "--no-archive", action="store_true",
        help="不使用下载归档（默认自动启用，避免重复下载）",
    )

    return p.parse_args()


def make_ytdlp_cmd(args: argparse.Namespace, url: str) -> list[str]:
    """构建带全部防封禁参数的 yt-dlp 命令。"""
    cmd = ["yt-dlp"]

    # ── 浏览器头（反爬规避） ──────────────────────────────────────────
    cmd += load_headers()

    # ── 防封禁核心 ──────────────────────────────────────────────────────
    cmd += ["--sleep-requests", str(args.sleep_requests)]
    cmd += ["--sleep-interval", str(args.sleep_interval[0])]
    cmd += ["--max-sleep-interval", str(args.sleep_interval[1])]
    cmd += ["--limit-rate", args.limit_rate]

    # ── 重试策略 ────────────────────────────────────────────────────────
    cmd += ["--retries", str(args.retries)]
    cmd += ["--file-access-retries", str(args.retries)]
    cmd += ["--fragment-retries", str(args.retries)]
    cmd += ["--retry-sleep", "5"]

    # ── Cookie 隔离 ─────────────────────────────────────────────────────
    if args.cookies:
        cmd += ["--cookies", str(args.cookies)]
    cmd += ["--no-cookies-from-browser"]

    # ── 输出结构 ────────────────────────────────────────────────────────
    output_path = str(args.output_dir / args.output_template)
    cmd += ["--output", output_path]

    if not args.no_archive:
        archive_path = args.output_dir / ".archive.txt"
        cmd += ["--download-archive", str(archive_path)]

    if args.metadata:
        cmd += ["--write-thumbnail", "--write-description"]
    else:
        cmd += ["--no-write-thumbnail", "--no-write-description"]

    # ── 通用 ────────────────────────────────────────────────────────────
    cmd += ["--ignore-errors"]
    cmd += ["--no-warnings"]
    cmd += ["--console-title"]

    cmd.append(url)
    return cmd


def run_batch(args: argparse.Namespace, urls: list[str]) -> int:
    """批量执行下载，每项之间随机休眠。"""
    total = len(urls)
    success = 0
    failed: list[tuple[str, str]] = []

    print(f"[batch] 总数: {total}")
    print(f"[batch] 设置: sleep_req={args.sleep_requests}s"
          f" | sleep_int=[{args.sleep_interval[0]}, {args.sleep_interval[1]}]s"
          f" | limit_rate={args.limit_rate}"
          f" | retries={args.retries}"
          f" | cookies={'✓' if args.cookies else '✗（无）'}"
          f" | output={args.output_dir}")

    if args.cookies:
        if not args.cookies.exists():
            print(f"[错误] cookies 文件不存在: {args.cookies}")
            return 1
        print(f"[batch] 使用 cookies: {args.cookies}")

    for i, url in enumerate(urls, 1):
        url = url.strip()
        if not url or url.startswith("#"):
            continue

        print(f"\n{'='*60}")
        print(f"[{i}/{total}] 处理: {url}")
        print(f"{'='*60}")

        cmd = make_ytdlp_cmd(args, url)
        result = subprocess.run(cmd, capture_output=False, text=False)

        if result.returncode == 0:
            success += 1
            print(f"[{i}/{total}] ✓ 完成")
        else:
            failed.append((url, f"exit code {result.returncode}"))
            print(f"[{i}/{total}] ✗ 失败（exit code {result.returncode}）")

        # ── 随机休眠（最后一项不休眠） ──────────────────────────────
        if i < total:
            delay = random.uniform(args.sleep_interval[0], args.sleep_interval[1])
            print(f"[batch] 休眠 {delay:.1f}s 后下载下一项...")
            time.sleep(delay)

    # ── 汇总报告 ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"下载完成: {success}/{total} 成功")
    if failed:
        print(f"失败列表:")
        for url, reason in failed:
            print(f"  ✗ {url}  —  {reason}")
    print(f"{'='*60}")

    return 0 if not failed else 1


def collect_urls(args: argparse.Namespace) -> list[str]:
    """从 --url / --urls-file / --playlist 收集待下载 URL。"""
    source_count = sum([
        1 if args.url else 0,
        1 if args.urls_file else 0,
        1 if args.playlist else 0,
    ])
    if source_count != 1:
        print("[错误] 请指定 --url / --urls-file / --playlist 其中之一")
        sys.exit(1)

    if args.url:
        return [args.url]

    if args.urls_file:
        if not args.urls_file.exists():
            print(f"[错误] URLs 文件不存在: {args.urls_file}")
            sys.exit(1)
        with open(args.urls_file) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if args.playlist:
        # yt-dlp --flat-playlist 快速列出 URL，不下载
        cmd = [
            "yt-dlp", "--flat-playlist", "--print", "url",
            "--no-warnings", "--no-cookies-from-browser",
        ]
        cmd += load_headers()
        if args.cookies:
            cmd.insert(-1, "--cookies")
            cmd.insert(-1, str(args.cookies))
        cmd.append(args.playlist)

        print(f"[batch] 正在解析播放列表: {args.playlist}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"[错误] 播放列表解析失败: {result.stderr.strip()}")
            sys.exit(1)

        urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        print(f"[batch] 播放列表包含 {len(urls)} 个视频")
        return urls

    return []


def main():
    args = parse_args()
    urls = collect_urls(args)
    if not urls:
        print("[错误] 没有找到任何 URL")
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.exit(run_batch(args, urls))


if __name__ == "__main__":
    main()
