#!/usr/bin/env python3
"""
获取 B站 用户收藏夹，记录到数据库，生成 URL 列表供 batch_dl.py 下载。

用法:
    # 根据 UID 列出收藏夹，交互选择
    python3 fetch_fav.py --uid 123456

    # 直接处理指定收藏夹
    python3 fetch_fav.py --media-id 12345

    # 不交互，直接列出来
    python3 fetch_fav.py --uid 123456 --list-only

    # 获取后自动下载（调用 batch_dl.py）
    python3 fetch_fav.py --uid 123456 --download

    # 获取后自动下载+处理（pipeline）
    python3 fetch_fav.py --uid 123456 --pipeline
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from db import DB


API_BASE = "https://api.bilibili.com"


def load_headers():
    cfg_path = Path(__file__).parent / "config.json"
    cfg = json.loads(cfg_path.read_text())
    headers = {"Referer": "https://www.bilibili.com/"}
    for h in cfg.get("download", {}).get("headers", []):
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


class BiliAPI:
    def __init__(self, headers: dict = None, cookies_file: str = ""):
        self.headers = headers or load_headers()
        self.cookies_file = cookies_file
        if cookies_file:
            self.headers["Cookie"] = self._load_cookies()

    def _load_cookies(self) -> str:
        """Load cookies from Netscape-format cookies.txt."""
        try:
            text = Path(self.cookies_file).read_text(encoding="utf-8")
        except Exception:
            return ""
        cookies = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                if any(d in line for d in [".bilibili.com", "bilibili.com"]):
                    cookies.append(f"{name}={value}")
        return "; ".join(cookies)

    def _request(self, url: str, retries: int = 3) -> dict | None:
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") != 0:
                    print(f"  API 错误: {data.get('message','unknown')} (url={url})", file=sys.stderr)
                    return None
                return data.get("data")
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                print(f"  请求失败: {e} (url={url})", file=sys.stderr)
                return None

    def get_created_folders(self, uid: str) -> list[dict]:
        data = self._request(f"{API_BASE}/x/v3/fav/folder/created?up_mid={uid}")
        return data.get("list", []) if data else []

    def get_collected_folders(self, uid: str) -> list[dict]:
        data = self._request(f"{API_BASE}/x/v3/fav/folder/collected?up_mid={uid}")
        return data.get("list", []) if data else []

    def get_folder_resources(self, media_id: str) -> tuple[list[dict], dict | None]:
        """Returns (medias_list, folder_info_dict)."""
        all_medias = []
        info = None
        pn = 1
        while True:
            data = self._request(
                f"{API_BASE}/x/v3/fav/resource/list?media_id={media_id}&pn={pn}&ps=20"
            )
            if not data:
                break
            if info is None:
                info = data.get("info", {})
            medias = data.get("medias", [])
            if medias is not None:
                all_medias.extend(medias)
            if not data.get("has_more"):
                break
            pn += 1
            time.sleep(0.5)
        return all_medias, info


def extract_uid(text: str) -> str:
    m = re.search(r"(?:space\.bilibili\.com|bilibili\.com/space)/(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"up_mid=(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"https?://space\.bilibili\.com/(\d+)", text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    return ""


def extract_media_id(text: str) -> str:
    m = re.search(r"[?&]fid=(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"media_id=(\d+)", text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    return ""


def aid_to_bv(aid: int) -> str:
    """Convert aid to BV format using the public algorithm or API."""
    return ""


def choose_folders(folders: list[dict]) -> list[dict]:
    if not folders:
        return []
    print(f"\n{'#':>3} | {'Media ID':<12} | {'视频数':>5} | {'标题'}")
    print("-" * 70)
    for i, f in enumerate(folders, 1):
        print(f"{i:>3} | {f['id']:<12} | {f.get('media_count', 0):>5} | {f.get('title', '(no title)')}")
    while True:
        try:
            inp = input("\n选择要下载的收藏夹（逗号分隔数字 / 范围如 1-5 / a=全部 / q=退出）: ").strip()
        except (EOFError, KeyboardInterrupt):
            return []
        if inp.lower() == "q":
            return []
        if inp.lower() == "a":
            return folders
        indices = set()
        for part in inp.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                indices.update(range(int(a.strip()), int(b.strip()) + 1))
            elif part:
                indices.add(int(part))
        selected = []
        for i in sorted(indices):
            if 1 <= i <= len(folders):
                selected.append(folders[i - 1])
            else:
                print(f"  跳过无效索引: {i}")
        if selected:
            return selected
        print("  未选择任何有效收藏夹，请重试")


def main():
    parser = argparse.ArgumentParser(description="Bilibili 收藏夹获取器")
    parser.add_argument("--uid", help="用户 UID 或空间 URL")
    parser.add_argument("--media-id", help="直接指定收藏夹 media_id")
    parser.add_argument("--source-uid", default="",
                        help="来源UID (标记这些视频来自谁的收藏夹，默认使用 --uid)")
    parser.add_argument("--cookies", default="",
                        help="cookies.txt 文件路径 (登录态，默认从 config.json 读取)")
    parser.add_argument("--list-only", action="store_true",
                        help="只列出收藏夹，不处理视频")
    parser.add_argument("--download", action="store_true",
                        help="获取后自动调用 batch_dl.py 下载")
    parser.add_argument("--pipeline", action="store_true",
                        help="获取后自动下载+处理（pipeline）")
    parser.add_argument("--output-dir", default="",
                        help="下载目录（默认: ~/Documents/bilibili/downloads 下的收藏夹名）")
    parser.add_argument("--download-args", default="",
                        help="传递给 batch_dl.py 的额外参数")
    args = parser.parse_args()

    if not args.uid and not args.media_id:
        parser.print_help()
        print("\n示例:")
        print("  python3 fetch_fav.py --uid 123456")
        print("  python3 fetch_fav.py --uid https://space.bilibili.com/123456")
        print("  python3 fetch_fav.py --media-id 12345 --download")
        return

    headers = load_headers()
    cookies_file = args.cookies or os.environ.get("BILIBILI_COOKIES", "")
    if cookies_file:
        cookies_file = str(Path(cookies_file).expanduser())
    api = BiliAPI(headers, cookies_file=cookies_file)
    db = DB()

    selected_folders = []

    if args.media_id:
        # Direct: fetch single folder
        resources, folder_info = api.get_folder_resources(args.media_id)
        if resources is None:
            print("无法获取该收藏夹数据")
            return
        title = folder_info.get("title", f"media_id_{args.media_id}") if folder_info else f"media_id_{args.media_id}"
        owner = {"mid": str(folder_info.get("upper", {}).get("mid", "")),
                 "name": folder_info.get("upper", {}).get("name", "")} if folder_info else {"mid": "", "name": ""}
        selected_folders = [{"id": args.media_id, "title": title,
                             "media_count": len(resources), "owner": owner}]
    elif args.uid:
        uid = extract_uid(args.uid)
        if not uid:
            print(f"无法解析 UID: {args.uid}")
            return
        print(f"正在获取 UID {uid} 的收藏夹...")
        created = api.get_created_folders(uid)
        collected = api.get_collected_folders(uid)
        all_folders = created + collected

        if not all_folders:
            print("未找到任何收藏夹（可能需要登录 cookie）")
            return

        print(f"\n共找到 {len(all_folders)} 个收藏夹")
        print(f"  - 自建: {len(created)}")
        print(f"  - 收藏: {len(collected)}")

        if args.list_only:
            for f in all_folders:
                print(f"  [{f['id']}] {f.get('title','')} ({f.get('media_count',0)} 视频)")
            return

        selected_folders = choose_folders(all_folders)
        if not selected_folders:
            print("已退出")
            return

    # Determine source_uid (the user whose favorites we're fetching)
    source_uid = ""
    if args.uid:
        source_uid = extract_uid(args.uid)
    if not source_uid:
        source_uid = args.source_uid or ""

    all_urls = []
    for folder in selected_folders:
        media_id = str(folder["id"])
        folder_title = folder.get("title", f"media_id_{media_id}")
        print(f"\n正在获取收藏夹「{folder_title}」(media_id={media_id})...")

        resources, _ = api.get_folder_resources(media_id)
        if not resources:
            print(f"  未获取到视频数据")
            continue

        # Record collection to DB
        owner = folder.get("owner", {})
        raw_data = json.dumps(resources, ensure_ascii=False)
        coll_id = db.add_collection(
            media_id=media_id,
            title=folder_title,
            owner_uid=str(owner.get("mid", "")),
            owner_name=owner.get("name", ""),
            description=folder.get("intro", ""),
            video_count=len(resources),
            raw_data=raw_data,
        )

        print(f"  共 {len(resources)} 个视频，正在记录到数据库...")
        folder_urls = []
        for idx, res in enumerate(resources, 1):
            bv = res.get("bv_id", "")
            aid = res.get("id", 0)
            title = res.get("title", "")
            owner_info = res.get("owner", {})
            uploader = owner_info.get("name", "")
            duration = res.get("duration", 0)
            intro = res.get("intro", "")

            if not bv and aid:
                print(f"    警告: 视频 #{idx} 无 BV 号 (aid={aid})，跳过")
                continue
            if not bv:
                continue

            url = f"https://www.bilibili.com/video/{bv}"

            video_id = db.add_video(
                bv=bv, url=url, title=title,
                uploader=uploader,
                description=intro,
                duration_sec=duration,
                source=f"favlist/{media_id}",
                source_uid=source_uid,
            )

            db.add_collection_video(coll_id, video_id, order_index=idx)
            folder_urls.append(url)

        all_urls.extend(folder_urls)
        print(f"  ✓ 已记录 {len(folder_urls)} 个视频到收藏夹 #{coll_id}")

    # Output URL list
    if not all_urls:
        print("\n没有需要下载的视频")
        db.close()
        return

    urls_file = Path(db.path).parent / "fav_urls.txt"
    urls_file.write_text("\n".join(all_urls) + "\n")
    print(f"\n✓ 共 {len(all_urls)} 个视频")
    print(f"  URL 列表已保存: {urls_file}")
    print(f"  收藏夹已记录到数据库")

    # Show matching collections in DB
    print("\n数据库中的收藏夹:")
    for c in db.list_collections():
        print(f"  #{c['id']} [{c['media_id']}] {c['title'][:50]} ({c['recorded_videos']}/{c['video_count']} 视频)")

    db.close()

    # Auto-download
    if args.download or args.pipeline:
        batch_dl = str(Path(__file__).parent / "batch_dl.py")
        cmd = [
            sys.executable, batch_dl,
            "--urls-file", str(urls_file),
        ]
        if args.output_dir:
            cmd.extend(["--output-dir", args.output_dir])
        if args.download_args:
            cmd.extend(args.download_args.split())

        print(f"\n启动下载: {' '.join(cmd)}")
        subprocess.run(cmd)

    if args.pipeline:
        print("\n处理完成后的下一步: 可以对下载的视频运行 pipeline.sh 或 batch_up.py")


if __name__ == "__main__":
    main()
