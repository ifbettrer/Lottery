from __future__ import annotations

import csv
import json
import mimetypes
import os
import secrets
import socket
import sqlite3
import threading
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIServer, make_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATABASE_PATH = Path(os.environ.get("LOTTERY_DB", PROJECT_ROOT / "lottery.db"))
MAX_NAME_LENGTH = 50
DB_LOCK = threading.RLock()
DEFAULT_THREAD_POOL_SIZE = 200


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = DEFAULT_THREAD_POOL_SIZE


ERROR_MESSAGES = {
    "INVALID_NAME": "姓名不能为空或格式不正确",
    "DUPLICATE_NAME": "不可重复取号",
    "NO_PARTICIPANTS": "暂无可抽奖人员",
    "NO_AVAILABLE_PARTICIPANTS": "暂无可抽奖人员",
    "INVALID_DRAW_REQUEST": "抽奖请求不合法",
    "INVALID_EXPORT_FORMAT": "导出格式不支持",
    "RESET_CONFIRM_REQUIRED": "请确认后再重置",
    "INVALID_RESET_SCOPE": "重置请求不合法",
    "STORAGE_ERROR": "系统繁忙，请稍后重试",
    "NOT_FOUND": "页面不存在",
}


ROUTES = {
    "/": "register.html",
    "/home": "home_join.html",
    "/home/join": "home_join.html",
    "/home/draw": "draw.html",
    "/register": "register.html",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def failure(code: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": ERROR_MESSAGES[code]}}


def normalize_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        return None
    return name


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: Path | None = None) -> None:
    with DB_LOCK:
        conn = connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    number INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS draws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL UNIQUE,
                    number INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    round INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (participant_id) REFERENCES participants(id)
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def register_participant(name_value: Any, db_path: Path | None = None) -> dict[str, Any]:
    name = normalize_name(name_value)
    if name is None:
        return failure("INVALID_NAME")

    with DB_LOCK:
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("SELECT id FROM participants WHERE name = ?", (name,)).fetchone()
            if duplicate:
                conn.rollback()
                return failure("DUPLICATE_NAME")
            next_number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM participants").fetchone()[0]
            created_at = utc_now()
            cursor = conn.execute(
                "INSERT INTO participants (name, number, created_at) VALUES (?, ?, ?)",
                (name, next_number, created_at),
            )
            conn.commit()
            return success({"id": cursor.lastrowid, "name": name, "number": next_number, "created_at": created_at})
        except sqlite3.IntegrityError:
            conn.rollback()
            return failure("DUPLICATE_NAME")
        except sqlite3.Error:
            conn.rollback()
            return failure("STORAGE_ERROR")
        finally:
            conn.close()


def draw_winner(payload: dict[str, Any] | None = None, db_path: Path | None = None) -> dict[str, Any]:
    payload = payload or {}
    count = payload.get("count", 1)
    exclude_winners = payload.get("exclude_winners", True)
    if count != 1 or exclude_winners is not True:
        return failure("INVALID_DRAW_REQUEST")

    with DB_LOCK:
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            participant_count = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
            if participant_count == 0:
                conn.rollback()
                return failure("NO_PARTICIPANTS")

            rows = conn.execute(
                """
                SELECT p.id, p.name, p.number
                FROM participants p
                LEFT JOIN draws d ON d.participant_id = p.id
                WHERE d.id IS NULL
                """
            ).fetchall()
            if not rows:
                conn.rollback()
                return failure("NO_AVAILABLE_PARTICIPANTS")

            winner = secrets.choice(rows)
            round_number = conn.execute("SELECT COALESCE(MAX(round), 0) + 1 FROM draws").fetchone()[0]
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO draws (participant_id, number, name, round, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (winner["id"], winner["number"], winner["name"], round_number, created_at),
            )
            conn.commit()
            return success(
                {
                    "winner": {"id": winner["id"], "name": winner["name"], "number": winner["number"]},
                    "draw": {"round": round_number, "created_at": created_at},
                }
            )
        except sqlite3.Error:
            conn.rollback()
            return failure("STORAGE_ERROR")
        finally:
            conn.close()


def list_available_participants(db_path: Path | None = None) -> dict[str, Any]:
    with DB_LOCK:
        conn = connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT p.id, p.name, p.number
                FROM participants p
                LEFT JOIN draws d ON d.participant_id = p.id
                WHERE d.id IS NULL
                ORDER BY p.number
                """
            ).fetchall()
            participants = [{"id": row["id"], "name": row["name"], "number": row["number"]} for row in rows]
            return success({"participants": participants})
        except sqlite3.Error:
            return failure("STORAGE_ERROR")
        finally:
            conn.close()


def export_winners(format_value: str = "csv", db_path: Path | None = None) -> tuple[bool, str | dict[str, Any]]:
    if format_value != "csv":
        return False, failure("INVALID_EXPORT_FORMAT")
    with DB_LOCK:
        conn = connect(db_path)
        try:
            rows = conn.execute("SELECT round, number, name, created_at FROM draws ORDER BY round").fetchall()
        except sqlite3.Error:
            return False, failure("STORAGE_ERROR")
        finally:
            conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["round", "number", "name", "created_at"])
    for row in rows:
        writer.writerow([row["round"], row["number"], row["name"], row["created_at"]])
    return True, output.getvalue()


def reset_activity(payload: dict[str, Any] | None = None, db_path: Path | None = None) -> dict[str, Any]:
    payload = payload or {}
    if payload.get("confirm") is not True:
        return failure("RESET_CONFIRM_REQUIRED")
    if payload.get("reset_scope", "all") != "all":
        return failure("INVALID_RESET_SCOPE")

    with DB_LOCK:
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM draws")
            conn.execute("DELETE FROM participants")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('draws', 'participants')")
            reset_at = utc_now()
            conn.commit()
            return success({"reset": True, "reset_scope": "all", "reset_at": reset_at})
        except sqlite3.Error:
            conn.rollback()
            return failure("STORAGE_ERROR")
        finally:
            conn.close()


def json_response(start_response, body: dict[str, Any], status: str = "200 OK"):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))])
    return [data]


def static_response(start_response, file_path: Path):
    try:
        data = file_path.read_bytes()
    except OSError:
        return json_response(start_response, failure("NOT_FOUND"), "404 Not Found")
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type == "application/javascript":
        content_type = f"{content_type}; charset=utf-8"
    start_response("200 OK", [("Content-Type", content_type), ("Content-Length", str(len(data)))])
    return [data]


def read_json(environ: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        size = 0
    raw = environ["wsgi.input"].read(size) if size else b"{}"
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, {}
    return isinstance(parsed, dict), parsed if isinstance(parsed, dict) else {}


def frontend_path(path: str) -> Path | None:
    if path in ROUTES:
        candidate = FRONTEND_DIR / ROUTES[path]
    elif path == "/lottery.png":
        candidate = PROJECT_ROOT / "lottery.png"
    elif path.startswith("/static/"):
        candidate = FRONTEND_DIR / path.removeprefix("/static/")
    else:
        return None

    resolved = candidate.resolve()
    allowed_roots = (FRONTEND_DIR.resolve(), PROJECT_ROOT.resolve())
    if not any(root in resolved.parents or resolved == root for root in allowed_roots):
        return None
    return resolved


def local_lan_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def app(environ: dict[str, Any], start_response):
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")

    if method == "GET":
        file_path = frontend_path(path)
        if file_path is not None:
            return static_response(start_response, file_path)

    if method == "POST" and path == "/api/register":
        ok, payload = read_json(environ)
        return json_response(start_response, register_participant(payload.get("name") if ok else None))
    if method == "POST" and path == "/api/draw":
        ok, payload = read_json(environ)
        return json_response(start_response, draw_winner(payload if ok else {"count": None}))
    if method == "GET" and path == "/api/participants":
        return json_response(start_response, list_available_participants())
    if method == "GET" and path == "/api/export":
        params = parse_qs(environ.get("QUERY_STRING", ""))
        format_value = params.get("format", ["csv"])[0]
        ok, exported = export_winners(format_value)
        if not ok:
            return json_response(start_response, exported)
        data = exported.encode("utf-8-sig")
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", 'attachment; filename="winners.csv"'),
                ("Content-Length", str(len(data))),
            ],
        )
        return [data]
    if method == "POST" and path == "/api/reset":
        ok, payload = read_json(environ)
        return json_response(start_response, reset_activity(payload if ok else {}))

    return json_response(start_response, failure("NOT_FOUND"), "404 Not Found")


def main() -> None:
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    with make_server(host, port, app, server_class=ThreadedWSGIServer) as server:
        print(f"Lottery server running locally at http://127.0.0.1:{port}/home")
        if host in {"", "0.0.0.0"}:
            lan_ip = local_lan_ip()
            if lan_ip:
                print(f"LAN access: http://{lan_ip}:{port}/home")
        print(f"Concurrent request server: threaded, queue size {server.request_queue_size}")
        server.serve_forever()


if __name__ == "__main__":
    main()
