#!/usr/bin/env python3
"""企业 Agent 问卷后端：零依赖（仅 Python 标准库 + SQLite）。

接口：
  GET  /health      -> {"ok": true}
  GET  /api/stats   -> {"yes": n, "no": n, "total": n}
  POST /api/vote    Body {"answer": "yes"|"no"} -> {"ok": true, "stats": {...}}

运行：python3 server.py [端口，默认 8000]
数据：同目录 votes.db（SQLite），可随时备份/导出。
"""
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path(__file__).with_name("votes.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    # WAL + 忙等待：现场几十上百人同时投票时，避免 SQLite 写锁冲突
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS votes ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  answer TEXT NOT NULL CHECK(answer IN ('yes','no')),"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))"
        ")"
    )
    conn.commit()
    return conn


def stats():
    conn = get_db()
    try:
        yes = conn.execute("SELECT COUNT(*) FROM votes WHERE answer='yes'").fetchone()[0]
        no = conn.execute("SELECT COUNT(*) FROM votes WHERE answer='no'").fetchone()[0]
    finally:
        conn.close()
    return {"yes": yes, "no": no, "total": yes + no}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 安静日志，只打一行
        sys.stderr.write("%s %s\n" % (self.address_string(), args[0] % args[1:]))

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        elif self.path == "/api/stats":
            self._send(200, stats())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/vote":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024:
            self._send(413, {"error": "body too large"})
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return
        answer = data.get("answer")
        if answer not in ("yes", "no"):
            self._send(400, {"error": "answer must be yes or no"})
            return
        conn = get_db()
        try:
            # 高并发下写锁重试
            for attempt in range(5):
                try:
                    conn.execute("INSERT INTO votes (answer) VALUES (?)", (answer,))
                    conn.commit()
                    break
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    import time
                    time.sleep(0.2 * (attempt + 1))
        finally:
            conn.close()
        self._send(201, {"ok": True, "stats": stats()})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    get_db()  # 建表
    print(f"问卷后端运行中：http://127.0.0.1:{port}  数据文件：{DB_PATH}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
