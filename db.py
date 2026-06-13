#!/usr/bin/env python3
"""
SQLite 数据库 — 记录 Bilibili 视频处理记录。

CLI:
    python3 db.py list                       # 列出所有视频
    python3 db.py search <bv/关键词>          # 搜索
    python3 db.py info <bv>                  # 视频详情
    python3 db.py stats                      # 统计
    python3 db.py export [-o file.json]      # 导出 JSON
    python3 db.py record video <json>        # 记录视频
    python3 db.py record transcript <json>
    python3 db.py record summary <json>
    python3 db.py record visual <json>
    python3 db.py record batch <json>

Import:
    from db import DB
    db = DB()
    db.add_video(bv="...", url="...", ...)
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def get_db_path() -> Path:
    base = os.environ.get("BILIBILI_OUTPUT_DIR")
    if not base:
        cfg_path = Path(__file__).parent / "config.json"
        if cfg_path.exists():
            import json as _json
            cfg = _json.loads(cfg_path.read_text())
            base = cfg.get("output_dir", "~/Documents/bilibili")
        else:
            base = "~/Documents/bilibili"
    base = Path(base).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base / "bilibili.db"


class DB:
    def __init__(self, db_path=None):
        self.path = Path(db_path or get_db_path())
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        self._migrate()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bv TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                uploader TEXT DEFAULT '',
                description TEXT DEFAULT '',
                duration_sec INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                source_uid TEXT DEFAULT '',
                t_status TEXT DEFAULT 'pending',
                t_model TEXT DEFAULT '',
                s_status TEXT DEFAULT 'pending',
                s_model TEXT DEFAULT '',
                v_status TEXT DEFAULT 'pending',
                v_model TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER REFERENCES videos(id),
                file_path TEXT,
                model TEXT DEFAULT 'whisper',
                char_count INTEGER DEFAULT 0,
                cost REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER REFERENCES videos(id),
                file_path TEXT,
                brief TEXT DEFAULT '',
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS visual_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER REFERENCES videos(id),
                file_path TEXT,
                frame_count INTEGER DEFAULT 0,
                cost REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS batch_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                type TEXT,
                video_ids TEXT,
                total_cost REAL DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                output_dir TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                owner_uid TEXT DEFAULT '',
                owner_name TEXT DEFAULT '',
                description TEXT DEFAULT '',
                video_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'favlist',
                raw_data TEXT,
                fetched_at TEXT DEFAULT (datetime('now')),
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS collection_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER REFERENCES collections(id),
                video_id INTEGER REFERENCES videos(id),
                order_index INTEGER DEFAULT 0,
                added_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_videos_bv ON videos(bv);
            CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title);
            CREATE INDEX IF NOT EXISTS idx_transcripts_video ON transcripts(video_id);
            CREATE INDEX IF NOT EXISTS idx_summaries_video ON summaries(video_id);
            CREATE INDEX IF NOT EXISTS idx_collections_mid ON collections(media_id);
            CREATE INDEX IF NOT EXISTS idx_cv_cid ON collection_videos(collection_id);
            CREATE INDEX IF NOT EXISTS idx_cv_vid ON collection_videos(video_id);
        """)
        self.conn.commit()

    def _migrate(self):
        """Add columns that may not exist in older database versions."""
        for table in ["videos", "summaries", "visual_analyses"]:
            existing = [r["name"] for r in self.conn.execute(
                f"PRAGMA table_info({table})").fetchall()]
            cols = []
            if table == "videos":
                cols = [
                    ("source_uid", "TEXT DEFAULT ''"),
                    ("t_status", "TEXT DEFAULT 'pending'"),
                    ("t_model", "TEXT DEFAULT ''"),
                    ("s_status", "TEXT DEFAULT 'pending'"),
                    ("s_model", "TEXT DEFAULT ''"),
                    ("v_status", "TEXT DEFAULT 'pending'"),
                    ("v_model", "TEXT DEFAULT ''"),
                ]
            elif table == "summaries":
                cols = [("model", "TEXT DEFAULT ''")]
            elif table == "visual_analyses":
                cols = [("model", "TEXT DEFAULT ''")]
            for col, coltype in cols:
                if col not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        self.conn.commit()
        # Backfill status from existing records
        self.conn.executescript("""
            UPDATE videos SET t_status='done', t_model=COALESCE(
                (SELECT model FROM transcripts WHERE video_id=videos.id ORDER BY id DESC LIMIT 1), 'whisper')
            WHERE id IN (SELECT DISTINCT video_id FROM transcripts) AND t_status='pending';
            UPDATE videos SET s_status='done', s_model=COALESCE(
                (SELECT model FROM summaries WHERE video_id=videos.id ORDER BY id DESC LIMIT 1), '')
            WHERE id IN (SELECT DISTINCT video_id FROM summaries) AND s_status='pending';
            UPDATE videos SET v_status='done', v_model=COALESCE(
                (SELECT model FROM visual_analyses WHERE video_id=videos.id ORDER BY id DESC LIMIT 1), '')
            WHERE id IN (SELECT DISTINCT video_id FROM visual_analyses) AND v_status='pending';
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ── Video ──────────────────────────────────────────────────────────

    def get_video_by_bv(self, bv: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM videos WHERE bv=?", (bv,)).fetchone()
        return dict(row) if row else None

    def add_video(self, bv: str, url: str, title: str = "", uploader: str = "",
                  description: str = "", duration_sec: int = 0, source: str = "manual",
                  source_uid: str = "") -> int:
        existing = self.get_video_by_bv(bv)
        if existing:
            updates = []
            params = []
            for k, v in [("title", title), ("uploader", uploader),
                         ("description", description), ("duration_sec", duration_sec),
                         ("source", source), ("source_uid", source_uid)]:
                if v:
                    updates.append(f"{k}=?")
                    params.append(v)
            if updates:
                params.append(bv)
                self.conn.execute(
                    f"UPDATE videos SET {', '.join(updates)}, updated_at=datetime('now') WHERE bv=?",
                    params,
                )
                self.conn.commit()
            return existing["id"]
        cur = self.conn.execute(
            "INSERT INTO videos (bv, url, title, uploader, description, duration_sec, source, source_uid) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bv, url, title, uploader, description, duration_sec, source, source_uid),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_video(self, **kw) -> int:
        bv = kw.pop("bv")
        existing = self.get_video_by_bv(bv)
        if existing:
            for k, v in kw.items():
                if v:
                    self.conn.execute(f"UPDATE videos SET {k}=?, updated_at=datetime('now') WHERE bv=?", (v, bv))
            self.conn.commit()
            return existing["id"]
        return self.add_video(bv=bv, **kw)

    # ── Transcript ─────────────────────────────────────────────────────

    def add_transcript(self, video_id: int, file_path: str, model: str = "whisper",
                       char_count: int = 0, cost: float = 0):
        self.conn.execute(
            "INSERT INTO transcripts (video_id, file_path, model, char_count, cost) VALUES (?,?,?,?,?)",
            (video_id, file_path, model, char_count, cost),
        )
        self.conn.execute(
            "UPDATE videos SET t_status='done', t_model=?, updated_at=datetime('now') WHERE id=?",
            (model, video_id),
        )
        self.conn.commit()

    # ── Summary ────────────────────────────────────────────────────────

    def add_summary(self, video_id: int, file_path: str, brief: str = "",
                    input_tokens: int = 0, output_tokens: int = 0, cost: float = 0,
                    model: str = ""):
        self.conn.execute(
            "INSERT INTO summaries (video_id, file_path, brief, input_tokens, output_tokens, cost, model) "
            "VALUES (?,?,?,?,?,?,?)",
            (video_id, file_path, brief[:500], input_tokens, output_tokens, cost, model),
        )
        self.conn.execute(
            "UPDATE videos SET s_status='done', s_model=?, updated_at=datetime('now') WHERE id=?",
            (model, video_id),
        )
        self.conn.commit()

    # ── Visual Analysis ────────────────────────────────────────────────

    def add_visual_analysis(self, video_id: int, file_path: str,
                            frame_count: int = 0, cost: float = 0,
                            model: str = ""):
        self.conn.execute(
            "INSERT INTO visual_analyses (video_id, file_path, frame_count, cost, model) "
            "VALUES (?,?,?,?,?)",
            (video_id, file_path, frame_count, cost, model),
        )
        self.conn.execute(
            "UPDATE videos SET v_status='done', v_model=?, updated_at=datetime('now') WHERE id=?",
            (model, video_id),
        )
        self.conn.commit()

    # ── Batch Job ──────────────────────────────────────────────────────

    def add_batch_job(self, name: str, type: str, video_ids: list,
                      total_cost: float = 0, output_dir: str = ""):
        self.conn.execute(
            "INSERT INTO batch_jobs (name, type, video_ids, total_cost, video_count, output_dir) "
            "VALUES (?,?,?,?,?,?)",
            (name, type, json.dumps(video_ids), total_cost, len(video_ids), output_dir),
        )
        self.conn.commit()

    # ── Collection ────────────────────────────────────────────────────

    def add_collection(self, media_id: str, title: str = "", owner_uid: str = "",
                       owner_name: str = "", description: str = "",
                       video_count: int = 0, raw_data: str = "",
                       source: str = "favlist") -> int:
        existing = self.get_collection_by_media_id(media_id)
        if existing:
            self.conn.execute(
                "UPDATE collections SET title=?, owner_uid=?, owner_name=?, "
                "description=?, video_count=?, raw_data=?, fetched_at=datetime('now') "
                "WHERE media_id=?",
                (title, owner_uid, owner_name, description, video_count, raw_data, media_id),
            )
            self.conn.commit()
            return existing["id"]
        cur = self.conn.execute(
            "INSERT INTO collections (media_id, title, owner_uid, owner_name, "
            "description, video_count, raw_data, source) VALUES (?,?,?,?,?,?,?,?)",
            (media_id, title, owner_uid, owner_name, description, video_count, raw_data, source),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_collection_by_media_id(self, media_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM collections WHERE media_id=?", (media_id,)).fetchone()
        return dict(row) if row else None

    def add_collection_video(self, collection_id: int, video_id: int, order_index: int = 0):
        existing = self.conn.execute(
            "SELECT * FROM collection_videos WHERE collection_id=? AND video_id=?",
            (collection_id, video_id)).fetchone()
        if existing:
            return
        self.conn.execute(
            "INSERT INTO collection_videos (collection_id, video_id, order_index) "
            "VALUES (?,?,?)",
            (collection_id, video_id, order_index),
        )
        self.conn.commit()

    def list_collections(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.*, COUNT(cv.id) as recorded_videos "
            "FROM collections c "
            "LEFT JOIN collection_videos cv ON cv.collection_id=c.id "
            "GROUP BY c.id ORDER BY c.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_collection_detail(self, collection_id: int) -> dict | None:
        c = self.conn.execute(
            "SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        if not c:
            return None
        c = dict(c)
        c["videos"] = [dict(r) for r in self.conn.execute(
            "SELECT v.*, cv.order_index, cv.added_at "
            "FROM collection_videos cv "
            "JOIN videos v ON v.id=cv.video_id "
            "WHERE cv.collection_id=? "
            "ORDER BY cv.order_index", (collection_id,))]
        return c

    def delete_collection(self, collection_id: int):
        self.conn.execute("DELETE FROM collection_videos WHERE collection_id=?", (collection_id,))
        self.conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        self.conn.commit()

    # ── Query ──────────────────────────────────────────────────────────

    def list_videos(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT v.*, s.brief, s.cost as summary_cost, s.model as summary_model, "
            "t.cost as transcript_cost, t.model as transcript_model, "
            "va.cost as visual_cost, va.frame_count, va.model as visual_model "
            "FROM videos v "
            "LEFT JOIN (SELECT video_id, brief, cost, model FROM summaries ORDER BY id DESC LIMIT 1) s ON s.video_id=v.id "
            "LEFT JOIN (SELECT video_id, cost, model FROM transcripts ORDER BY id DESC LIMIT 1) t ON t.video_id=v.id "
            "LEFT JOIN (SELECT video_id, cost, frame_count, model FROM visual_analyses ORDER BY id DESC LIMIT 1) va ON va.video_id=v.id "
            "ORDER BY v.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 30) -> list[dict]:
        pattern = f"%{query}%"
        rows = self.conn.execute(
            "SELECT v.*, s.brief "
            "FROM videos v "
            "LEFT JOIN summaries s ON s.video_id=v.id "
            "WHERE v.bv LIKE ? OR v.title LIKE ? OR v.uploader LIKE ? OR v.description LIKE ? "
            "ORDER BY v.created_at DESC LIMIT ?",
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_video_detail(self, bv: str) -> dict | None:
        v = self.get_video_by_bv(bv)
        if not v:
            return None
        v["transcripts"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM transcripts WHERE video_id=? ORDER BY created_at DESC", (v["id"],))]
        v["summaries"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM summaries WHERE video_id=? ORDER BY created_at DESC", (v["id"],))]
        v["visual_analyses"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM visual_analyses WHERE video_id=? ORDER BY created_at DESC", (v["id"],))]
        return v

    def stats(self) -> dict:
        return dict(self.conn.execute("""
            SELECT
                COUNT(*) as video_count,
                COALESCE(SUM(duration_sec), 0) as total_duration_sec,
                (SELECT COUNT(*) FROM transcripts) as transcript_count,
                (SELECT COUNT(*) FROM summaries) as summary_count,
                (SELECT COUNT(*) FROM visual_analyses) as visual_count,
                (SELECT COALESCE(SUM(cost), 0) FROM transcripts) as transcript_total_cost,
                (SELECT COALESCE(SUM(cost), 0) FROM summaries) as summary_total_cost,
                (SELECT COALESCE(SUM(cost), 0) FROM visual_analyses) as visual_total_cost,
                (SELECT COALESCE(SUM(total_cost), 0) FROM batch_jobs) as batch_total_cost
            FROM videos
        """).fetchone())

    def export(self) -> list[dict]:
        videos = self.list_videos(limit=99999)
        for v in videos:
            v["detail"] = self.get_video_detail(v["bv"])
        return videos


# ── CLI ────────────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(description="Bilibili 视频处理数据库")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有视频")
    p_status = sub.add_parser("status", help="列出未处理视频")
    p_status.add_argument("step", nargs="?", choices=["transcript", "summary", "visual"],
                          default="transcript", help="检查哪个步骤的状态")
    p_search = sub.add_parser("search", help="搜索视频")
    p_search.add_argument("query", help="BV 号或关键词")

    p_info = sub.add_parser("info", help="视频详情")
    p_info.add_argument("bv", help="BV 号")

    sub.add_parser("stats", help="统计")

    sub.add_parser("collections", help="列出收藏夹")

    p_coll = sub.add_parser("collection", help="收藏夹操作")
    p_coll.add_argument("action", choices=["list", "delete"])
    p_coll.add_argument("id", help="收藏夹 ID")

    p_export = sub.add_parser("export", help="导出 JSON")
    p_export.add_argument("-o", "--output", help="输出文件")

    p_rec = sub.add_parser("record", help="记录数据")
    p_rec.add_argument("type", choices=["video", "transcript", "summary", "visual", "batch"])
    p_rec.add_argument("data", help="JSON 数据")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    db = DB()

    if args.cmd == "list":
        videos = db.list_videos()
        if not videos:
            print("(empty)")
            return
        print(f"{'BV':<20} {'Title':<45} {'T':>1} {'S':>1} {'V':>1}  {'Source':<15}")
        print("-" * 95)
        for v in videos:
            t = "T" if v.get("t_status") == "done" else "·"
            s = "S" if v.get("s_status") == "done" else "·"
            vis = "V" if v.get("v_status") == "done" else "·"
            src = v.get("source_uid", "") or v.get("source", "")[:13] or "manual"
            print(f"{v['bv']:<20} {v['title'][:43]:<45} {t:>1} {s:>1} {vis:>1}  {str(src)[:13]:<15}")

    elif args.cmd == "status":
        step_map = {"transcript": "t", "summary": "s", "visual": "v"}
        prefix = step_map[args.step]
        rows = db.conn.execute(
            f"SELECT bv, title, {prefix}_status, {prefix}_model, source_uid, "
            f"source FROM videos WHERE {prefix}_status != 'done' "
            f"ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        if not rows:
            print(f"全部视频的「{args.step}」已完成")
            return
        print(f"以下视频未完成 {args.step} 处理:")
        print(f"{'BV':<20} {'Title':<45} {'Status':<10} {'Source':<15}")
        print("-" * 95)
        for r in rows:
            src = r["source_uid"] or r["source"][:13] or "manual"
            print(f"{r['bv']:<20} {r['title'][:43]:<45} {r[f'{prefix}_status']:<10} {str(src)[:13]:<15}")

    elif args.cmd == "search":
        results = db.search(args.query)
        if not results:
            print("未找到匹配结果")
            return
        for v in results:
            brief = v.get("brief", "") or ""
            print(f"[{v['bv']}] {v['title']}")
            if brief:
                print(f"  简述: {brief[:100]}")
            print()

    elif args.cmd == "info":
        detail = db.get_video_detail(args.bv)
        if not detail:
            print(f"未找到: {args.bv}")
            return
        print(f"BV:        {detail['bv']}")
        print(f"URL:       {detail['url']}")
        print(f"标题:      {detail['title']}")
        print(f"UP主:      {detail.get('uploader','')}")
        print(f"简介:      {detail.get('description','')[:200]}")
        print(f"时长:      {detail.get('duration_sec',0)}s")
        print(f"来源:      {detail.get('source','')}")
        print(f"来源UID:   {detail.get('source_uid','')}")
        print(f"入库:      {detail.get('created_at','')}")
        print(f"状态:      转录={detail.get('t_status','?')} 汇总={detail.get('s_status','?')} 视觉={detail.get('v_status','?')}")
        print(f"模型:      转录={detail.get('t_model','-')} 汇总={detail.get('s_model','-')} 视觉={detail.get('v_model','-')}")
        print()
        for s in detail.get("summaries", []):
            print(f"├ 汇总: {s.get('brief','')[:200]}")
            print(f"├ 费用: ¥{s.get('cost',0):.4f}")
            print(f"├ tokens: {s.get('input_tokens',0)} in / {s.get('output_tokens',0)} out")

    elif args.cmd == "stats":
        s = db.stats()
        print(f"视频总数:     {s['video_count']}")
        print(f"总时长:       {s['total_duration_sec']//60} 分 {s['total_duration_sec']%60} 秒")
        print(f"转录记录:     {s['transcript_count']}")
        print(f"汇总记录:     {s['summary_count']}")
        print(f"视觉分析:     {s['visual_count']}")
        print()
        total = (s['transcript_total_cost'] + s['summary_total_cost']
                 + s['visual_total_cost'] + s['batch_total_cost'])
        print(f"转录费用:     ¥{s['transcript_total_cost']:.4f}")
        print(f"汇总费用:     ¥{s['summary_total_cost']:.4f}")
        print(f"视觉分析费用: ¥{s['visual_total_cost']:.4f}")
        print(f"批量任务费用: ¥{s['batch_total_cost']:.4f}")
        print(f"总费用:       ¥{total:.4f}")

    elif args.cmd == "collections":
        cols = db.list_collections()
        if not cols:
            print("(empty)")
            return
        print(f"{'ID':<5} {'Media ID':<15} {'Title':<40} {'Owner':<20} {'Videos':>7}")
        print("-" * 90)
        for c in cols:
            print(f"{c['id']:<5} {c['media_id']:<15} {c['title'][:38]:<40} {c.get('owner_name','')[:18]:<20} {c['recorded_videos']:>7}")

    elif args.cmd == "collection":
        if args.action == "list":
            items = db.get_collection_detail(int(args.id))
            if not items:
                print("未找到收藏夹")
                return
            print(f"收藏夹: {items['title']} (media_id={items['media_id']})")
            print(f"UP主:    {items.get('owner_name','')} (uid={items.get('owner_uid','')})")
            print(f"记录:    {len(items['videos'])}/{items['video_count']} 个视频\n")
            for v in items["videos"]:
                print(f"  [{v['order_index']:>3}] {v['bv']} {v['title'][:60]}")
        elif args.action == "delete":
            db.delete_collection(int(args.id))
            print(f"收藏夹 #{args.id} 已删除")

    elif args.cmd == "export":
        data = db.export()
        out = args.output or os.path.join(os.path.dirname(db.path), "export.json")
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"导出 {len(data)} 条记录到: {out}")

    elif args.cmd == "record":
        data = json.loads(args.data)
        t = args.type
        if t == "video":
            db.upsert_video(**data)
        elif t == "transcript":
            db.add_transcript(**data)
        elif t == "summary":
            db.add_summary(**data)
        elif t == "visual":
            db.add_visual_analysis(**data)
        elif t == "batch":
            db.add_batch_job(**data)
        print(f"[db] {t} recorded")

    db.close()


if __name__ == "__main__":
    cli()
