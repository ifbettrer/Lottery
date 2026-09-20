from __future__ import annotations

import csv
import hmac
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
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import WSGIServer, make_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATABASE_PATH = Path(os.environ.get("LOTTERY_DB", PROJECT_ROOT / "lottery.db"))
MAX_NAME_LENGTH = 50
PHONE_LENGTH = 11
HOME_PASSWORD = os.environ.get("HOME_PASSWORD", "123456")
HOME_AUTH_COOKIE = "lottery_home_auth"
HOME_AUTH_TOKEN = os.environ.get("HOME_AUTH_TOKEN", secrets.token_urlsafe(32))
DB_LOCK = threading.RLock()
DEFAULT_THREAD_POOL_SIZE = 200


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = DEFAULT_THREAD_POOL_SIZE


ERROR_MESSAGES = {
    "INVALID_NAME": "姓名不能为空或格式不正确",
    "DUPLICATE_NAME": "不可重复取号",
    "INVALID_PHONE": "手机号不能为空或格式不正确",
    "PHONE_TOO_SHORT": "电话号码不足11位",
    "DUPLICATE_PHONE": "不能重复取号",
    "NO_PARTICIPANTS": "暂无可抽奖人员",
    "NO_AVAILABLE_PARTICIPANTS": "暂无可抽奖人员",
    "INVALID_DRAW_REQUEST": "抽奖请求不合法",
    "INVALID_EXPORT_FORMAT": "导出格式不支持",
    "RESET_CONFIRM_REQUIRED": "请确认后再重置",
    "INVALID_RESET_SCOPE": "重置请求不合法",
    "STORAGE_ERROR": "系统繁忙，请稍后重试",
    "UNAUTHORIZED": "请先输入访问密码",
    "NOT_FOUND": "页面不存在",
}


ROUTES = {
    "/": "register.html",
    "/home/login": "home_login.html",
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


def normalize_phone(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "INVALID_PHONE"
    phone = value.strip()
    if phone and not phone.isdigit():
        return None, "INVALID_PHONE"
    if len(phone) < PHONE_LENGTH:
        return None, "PHONE_TOO_SHORT"
    if len(phone) != PHONE_LENGTH:
        return None, "INVALID_PHONE"
    return phone, None


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
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE,
                    number INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS draws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL UNIQUE,
                    number INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (participant_id) REFERENCES participants(id)
                );
                """
            )
            participant_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(participants)").fetchall()
            }
            participants_schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'participants'"
            ).fetchone()
            needs_participant_migration = (
                "phone" not in participant_columns
                or bool(participants_schema and "name TEXT NOT NULL UNIQUE" in participants_schema["sql"])
            )
            if needs_participant_migration:
                conn.executescript(
                    """
                    CREATE TABLE participants_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        phone TEXT NOT NULL UNIQUE,
                        number INTEGER NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO participants_new (id, name, phone, number, created_at)
                    SELECT id, name, printf('legacy%04d', id), number, created_at FROM participants;
                    DROP TABLE participants;
                    ALTER TABLE participants_new RENAME TO participants;
                    """
                )
            draws_schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'draws'"
            ).fetchone()
            if draws_schema and "round INTEGER NOT NULL UNIQUE" in draws_schema["sql"]:
                conn.executescript(
                    """
                    CREATE TABLE draws_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        participant_id INTEGER NOT NULL UNIQUE,
                        number INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        round INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (participant_id) REFERENCES participants(id)
                    );
                    INSERT INTO draws_new (id, participant_id, number, name, round, created_at)
                    SELECT id, participant_id, number, name, round, created_at FROM draws;
                    DROP TABLE draws;
                    ALTER TABLE draws_new RENAME TO draws;
                    """
                )
            conn.commit()
        finally:
            conn.close()


def register_participant(
    name_value: Any, phone_value: Any | None = None, db_path: Path | None = None
) -> dict[str, Any]:
    name = normalize_name(name_value)
    if name is None:
        return failure("INVALID_NAME")
    phone, phone_error = normalize_phone(phone_value)
    if phone_error is not None:
        return failure(phone_error)

    with DB_LOCK:
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT id, name, number FROM participants WHERE phone = ?", (phone,)
            ).fetchone()
            if duplicate:
                conn.rollback()
                result = failure("DUPLICATE_PHONE")
                result["data"] = {
                    "id": duplicate["id"],
                    "name": duplicate["name"],
                    "phone": phone,
                    "number": duplicate["number"],
                }
                return result
            next_number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM participants").fetchone()[0]
            created_at = utc_now()
            cursor = conn.execute(
                "INSERT INTO participants (name, phone, number, created_at) VALUES (?, ?, ?, ?)",
                (name, phone, next_number, created_at),
            )
            conn.commit()
            return success(
                {
                    "id": cursor.lastrowid,
                    "name": name,
                    "phone": phone,
                    "number": next_number,
                    "created_at": created_at,
                }
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return failure("DUPLICATE_PHONE")
        except sqlite3.Error:
            conn.rollback()
            return failure("STORAGE_ERROR")
        finally:
            conn.close()


def draw_winner(payload: dict[str, Any] | None = None, db_path: Path | None = None) -> dict[str, Any]:
    payload = payload or {}
    count = payload.get("count", 1)
    exclude_winners = payload.get("exclude_winners", True)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or exclude_winners is not True:
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
                SELECT p.id, p.name, p.phone, p.number
                FROM participants p
                LEFT JOIN draws d ON d.participant_id = p.id
                WHERE d.id IS NULL
                """
            ).fetchall()
            if not rows:
                conn.rollback()
                return failure("NO_AVAILABLE_PARTICIPANTS")
            if count > len(rows):
                conn.rollback()
                return failure("NO_AVAILABLE_PARTICIPANTS")

            remaining = list(rows)
            winners = []
            for _ in range(count):
                winner = secrets.choice(remaining)
                winners.append(winner)
                remaining.remove(winner)
            round_number = conn.execute("SELECT COALESCE(MAX(round), 0) + 1 FROM draws").fetchone()[0]
            created_at = utc_now()
            conn.executemany(
                """
                INSERT INTO draws (participant_id, number, name, round, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (winner["id"], winner["number"], winner["name"], round_number, created_at)
                    for winner in winners
                ],
            )
            conn.commit()
            winner_data = [
                {"id": winner["id"], "name": winner["name"], "phone": winner["phone"], "number": winner["number"]}
                for winner in winners
            ]
            return success(
                {
                    "winner": winner_data[0],
                    "winners": winner_data,
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
            rows = conn.execute(
                """
                SELECT d.round, d.number, d.name, p.phone, d.created_at
                FROM draws d
                JOIN participants p ON p.id = d.participant_id
                ORDER BY d.round, d.id
                """
            ).fetchall()
        except sqlite3.Error:
            return False, failure("STORAGE_ERROR")
        finally:
            conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["round", "number", "name", "phone", "created_at"])
    for row in rows:
        writer.writerow([row["round"], row["number"], row["name"], row["phone"], row["created_at"]])
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


def redirect_response(start_response, location: str, headers: list[tuple[str, str]] | None = None):
    body = b""
    response_headers = [("Location", location), ("Content-Length", "0")]
    if headers:
        response_headers.extend(headers)
    start_response("302 Found", response_headers)
    return [body]


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


def read_form(environ: dict[str, Any]) -> dict[str, str]:
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        size = 0
    raw = environ["wsgi.input"].read(size) if size else b""
    params = parse_qs(raw.decode("utf-8", errors="replace"))
    return {key: values[0] for key, values in params.items() if values}


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


def cookie_values(environ: dict[str, Any]) -> dict[str, str]:
    cookies = {}
    for part in environ.get("HTTP_COOKIE", "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def is_home_authorized(environ: dict[str, Any]) -> bool:
    token = cookie_values(environ).get(HOME_AUTH_COOKIE, "")
    return bool(token) and hmac.compare_digest(token, HOME_AUTH_TOKEN)


def home_login_location(environ: dict[str, Any]) -> str:
    path = environ.get("PATH_INFO", "/home")
    query = environ.get("QUERY_STRING", "")
    target = path if not query else f"{path}?{query}"
    return "/home/login?" + urlencode({"next": target})


def requires_home_auth(path: str) -> bool:
    return path in {"/home", "/home/join", "/home/draw"}


def requires_admin_api_auth(path: str) -> bool:
    return path in {"/api/draw", "/api/participants", "/api/export", "/api/reset"}


def frontend_path(path: str) -> Path | None:
    if path in ROUTES:
        candidate = FRONTEND_DIR / ROUTES[path]
    elif path in {"/lottery.png", "/wedding.jpg", "/bg.jpg", "/verti_bg.jpg"}:
        candidate = PROJECT_ROOT / path.removeprefix("/")
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

    if method == "POST" and path == "/home/login":
        form = read_form(environ)
        params = parse_qs(environ.get("QUERY_STRING", ""))
        next_url = params.get("next", ["/home"])[0]
        if not next_url.startswith("/home") or next_url.startswith("/home/login"):
            next_url = "/home"
        if hmac.compare_digest(form.get("password", ""), HOME_PASSWORD):
            return redirect_response(
                start_response,
                next_url,
                [
                    (
                        "Set-Cookie",
                        f"{HOME_AUTH_COOKIE}={HOME_AUTH_TOKEN}; Path=/; HttpOnly; SameSite=Lax",
                    )
                ],
            )
        return redirect_response(start_response, "/home/login?error=1&" + urlencode({"next": next_url}))

    if method == "POST" and path == "/home/logout":
        return redirect_response(
            start_response,
            "/home/login?logged_out=1",
            [
                (
                    "Set-Cookie",
                    f"{HOME_AUTH_COOKIE}=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; "
                    "HttpOnly; SameSite=Lax",
                )
            ],
        )

    if requires_home_auth(path) and not is_home_authorized(environ):
        return redirect_response(start_response, home_login_location(environ))

    if requires_admin_api_auth(path) and not is_home_authorized(environ):
        return json_response(start_response, failure("UNAUTHORIZED"), "401 Unauthorized")

    if method == "GET":
        file_path = frontend_path(path)
        if file_path is not None:
            return static_response(start_response, file_path)

    if method == "POST" and path == "/api/register":
        ok, payload = read_json(environ)
        if not ok:
            payload = {}
        return json_response(start_response, register_participant(payload.get("name"), payload.get("phone")))
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
