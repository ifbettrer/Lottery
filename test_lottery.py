from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from socketserver import ThreadingMixIn
from unittest.mock import patch

from backend import app as backend_app


class LotterySpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "lottery.db"
        self.phone_index = 0
        backend_app.init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def register(self, name, phone=None):
        if phone is None:
            self.phone_index += 1
            phone = f"1390000{self.phone_index:04d}"
        return backend_app.register_participant(name, phone, self.db_path)

    def draw(self, payload=None):
        return backend_app.draw_winner(payload, self.db_path)

    def reset(self, payload=None):
        return backend_app.reset_activity(payload, self.db_path)

    def test_first_registration_success(self):
        result = self.register("张三")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["name"], "张三")
        self.assertEqual(result["data"]["phone"], "13900000001")
        self.assertEqual(result["data"]["number"], 1)

    def test_registration_trims_outer_spaces(self):
        result = self.register("  李四  ")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["name"], "李四")

    def test_multiple_registrations_get_unique_incrementing_numbers(self):
        numbers = [self.register(name)["data"]["number"] for name in ["张三", "李四", "王五"]]
        self.assertEqual(numbers, [1, 2, 3])

    def test_draw_success_returns_winner_and_round(self):
        self.register("张三")
        result = self.draw()
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["draw"]["round"], 1)
        self.assertEqual(result["data"]["winner"]["name"], "张三")
        self.assertEqual(result["data"]["winner"]["number"], 1)
        self.assertEqual(result["data"]["winners"], [result["data"]["winner"]])

    def test_draw_multiple_winners_without_duplicates(self):
        for name in ["张三", "李四", "王五", "赵六"]:
            self.register(name)

        result = self.draw({"count": 3, "exclude_winners": True})

        self.assertTrue(result["success"])
        winners = result["data"]["winners"]
        self.assertEqual(len(winners), 3)
        self.assertEqual(len({winner["id"] for winner in winners}), 3)
        self.assertEqual(result["data"]["winner"], winners[0])
        self.assertEqual(result["data"]["draw"]["round"], 1)

        available = backend_app.list_available_participants(self.db_path)
        self.assertEqual(len(available["data"]["participants"]), 1)

    def test_draw_more_than_available_participants_fails(self):
        self.register("张三")
        self.register("李四")

        result = self.draw({"count": 3, "exclude_winners": True})

        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "NO_AVAILABLE_PARTICIPANTS")

    def test_draw_uses_random_choice_from_available_participants(self):
        for name in ["张三", "李四", "王五"]:
            self.register(name)

        captured_candidates = []

        def choose_last(candidates):
            captured_candidates.extend(row["name"] for row in candidates)
            return candidates[-1]

        with patch.object(backend_app.secrets, "choice", side_effect=choose_last) as random_choice:
            result = self.draw()

        self.assertTrue(result["success"])
        self.assertEqual(random_choice.call_count, 1)
        self.assertEqual(captured_candidates, ["张三", "李四", "王五"])
        self.assertEqual(result["data"]["winner"]["name"], "王五")

    def test_draw_record_is_exported_with_matching_data(self):
        registered = self.register("张三", "13800138000")
        draw = self.draw()
        ok, exported = backend_app.export_winners("csv", self.db_path)
        rows = list(csv.DictReader(exported.splitlines()))
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["round"], str(draw["data"]["draw"]["round"]))
        self.assertEqual(rows[0]["number"], str(draw["data"]["winner"]["number"]))
        self.assertEqual(rows[0]["name"], draw["data"]["winner"]["name"])
        self.assertEqual(rows[0]["phone"], registered["data"]["phone"])
        self.assertTrue(rows[0]["created_at"])

    def test_pages_contain_required_controls(self):
        register_page = (backend_app.FRONTEND_DIR / "register.html").read_text(encoding="utf-8")
        home_join_page = (backend_app.FRONTEND_DIR / "home_join.html").read_text(encoding="utf-8")
        draw_page = (backend_app.FRONTEND_DIR / "draw.html").read_text(encoding="utf-8")
        register_script = (backend_app.FRONTEND_DIR / "register.js").read_text(encoding="utf-8")
        draw_script = (backend_app.FRONTEND_DIR / "draw.js").read_text(encoding="utf-8")
        self.assertIn("扫码取号", register_page)
        self.assertIn("姓名", register_page)
        self.assertIn("手机号", register_page)
        self.assertIn("提交取号", register_page)
        self.assertIn("参与抽奖", home_join_page)
        self.assertIn("/home/draw", home_join_page)
        self.assertIn("/lottery.png", home_join_page)
        self.assertIn("/home/join", draw_page)
        self.assertIn("开始抽奖", draw_page)
        self.assertIn("导出中奖记录", draw_page)
        self.assertIn("重置活动", draw_page)
        self.assertIn("/api/register", register_script)
        self.assertIn("/api/draw", draw_script)
        self.assertIn("/api/participants", draw_script)
        self.assertIn("/api/reset", draw_script)

    def test_multiple_draws_permanently_exclude_winners(self):
        for name in ["张三", "李四", "王五"]:
            self.register(name)
        draws = [self.draw() for _ in range(3)]
        self.assertTrue(all(result["success"] for result in draws))
        names = [result["data"]["winner"]["name"] for result in draws]
        rounds = [result["data"]["draw"]["round"] for result in draws]
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(rounds, [1, 2, 3])

    def test_available_participants_excludes_previous_winners(self):
        for name in ["张三", "李四", "王五"]:
            self.register(name)
        with patch.object(backend_app.secrets, "choice", side_effect=lambda candidates: candidates[1]):
            self.draw()

        result = backend_app.list_available_participants(self.db_path)

        self.assertTrue(result["success"])
        self.assertEqual([row["name"] for row in result["data"]["participants"]], ["张三", "王五"])

    def test_reset_clears_data_and_restarts_numbering(self):
        self.register("张三")
        self.draw()
        result = self.reset({"confirm": True, "reset_scope": "all"})
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["reset"])
        next_registration = self.register("张三")
        self.assertTrue(next_registration["success"])
        self.assertEqual(next_registration["data"]["number"], 1)

    def test_invalid_names_are_rejected(self):
        for value in ["", "   ", "A" * 51, 123]:
            with self.subTest(value=value):
                result = self.register(value)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "INVALID_NAME")

    def test_duplicate_names_are_allowed_with_different_phones(self):
        self.assertTrue(self.register("张三")["success"])
        result = self.register(" 张三 ")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["name"], "张三")
        self.assertEqual(result["data"]["number"], 2)

    def test_invalid_phone_numbers_are_rejected(self):
        for value, code, message in [
            ("", "PHONE_TOO_SHORT", "电话号码不足11位"),
            ("1234567890", "PHONE_TOO_SHORT", "电话号码不足11位"),
            ("1380013800a", "INVALID_PHONE", "手机号不能为空或格式不正确"),
            ("138-013800", "INVALID_PHONE", "手机号不能为空或格式不正确"),
            ("123456789012", "INVALID_PHONE", "手机号不能为空或格式不正确"),
            (12345678901, "INVALID_PHONE", "手机号不能为空或格式不正确"),
        ]:
            with self.subTest(value=value):
                result = backend_app.register_participant("张三", value, self.db_path)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["error"]["message"], message)

    def test_duplicate_phone_is_rejected_with_existing_number(self):
        first = self.register("张三", "13800138000")
        second = self.register("李四", "13800138000")
        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["code"], "DUPLICATE_PHONE")
        self.assertEqual(second["error"]["message"], "不能重复取号")
        self.assertEqual(second["data"]["name"], first["data"]["name"])
        self.assertEqual(second["data"]["number"], first["data"]["number"])

    def test_duplicate_phone_is_checked_from_existing_database_after_restart(self):
        first = self.register("张三", "13800138000")
        backend_app.init_db(self.db_path)

        second = backend_app.register_participant("李四", "13800138000", self.db_path)

        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["code"], "DUPLICATE_PHONE")
        self.assertEqual(second["data"]["number"], first["data"]["number"])

    def test_draw_without_participants_fails(self):
        result = self.draw()
        self.assertFalse(result["success"])
        self.assertIn(result["error"]["code"], {"NO_PARTICIPANTS", "NO_AVAILABLE_PARTICIPANTS"})

    def test_draw_after_all_participants_won_fails(self):
        self.register("张三")
        self.assertTrue(self.draw()["success"])
        result = self.draw()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "NO_AVAILABLE_PARTICIPANTS")

    def test_invalid_draw_requests_are_rejected(self):
        self.register("张三")
        for payload in [{"count": 0}, {"exclude_winners": False}, {"count": "1"}]:
            with self.subTest(payload=payload):
                result = self.draw(payload)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "INVALID_DRAW_REQUEST")

    def test_invalid_export_format_is_rejected(self):
        ok, result = backend_app.export_winners("xlsx", self.db_path)
        self.assertFalse(ok)
        self.assertEqual(result["error"]["code"], "INVALID_EXPORT_FORMAT")

    def test_reset_requires_confirmation_and_supported_scope(self):
        for payload, code in [
            ({}, "RESET_CONFIRM_REQUIRED"),
            ({"confirm": False}, "RESET_CONFIRM_REQUIRED"),
            ({"confirm": True, "reset_scope": "winners_only"}, "INVALID_RESET_SCOPE"),
        ]:
            with self.subTest(payload=payload):
                result = self.reset(payload)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], code)

    def test_shortest_and_longest_names_are_accepted(self):
        self.assertTrue(self.register("A")["success"])
        self.assertTrue(self.register("B" * 50)["success"])

    def test_roughly_100_concurrent_registrations(self):
        names = [f"用户{i:03d}" for i in range(100)]
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(self.register, names))
        self.assertTrue(all(result["success"] for result in results))
        numbers = sorted(result["data"]["number"] for result in results)
        self.assertEqual(numbers, list(range(1, 101)))

    def test_concurrent_duplicate_phone_allows_only_one_success(self):
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: self.register("张三", "13800138000"), range(10)))
        success_count = sum(1 for result in results if result["success"])
        duplicate_count = sum(1 for result in results if not result["success"] and result["error"]["code"] == "DUPLICATE_PHONE")
        self.assertEqual(success_count, 1)
        self.assertEqual(duplicate_count, 9)

    def test_single_available_participant_can_win_once(self):
        self.register("张三")
        first = self.draw()
        second = self.draw()
        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["code"], "NO_AVAILABLE_PARTICIPANTS")

    def test_concurrent_draws_do_not_duplicate_winners(self):
        for index in range(20):
            self.register(f"用户{index}")
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: self.draw(), range(20)))
        winners = [result["data"]["winner"]["id"] for result in results if result["success"]]
        rounds = [result["data"]["draw"]["round"] for result in results if result["success"]]
        self.assertEqual(len(winners), 20)
        self.assertEqual(len(set(winners)), 20)
        self.assertEqual(sorted(rounds), list(range(1, 21)))

    def test_script_like_name_is_not_executed_and_can_be_exported_safely(self):
        name = "<script>alert(1)</script>"
        result = self.register(name)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["name"], name)
        self.draw()
        ok, exported = backend_app.export_winners("csv", self.db_path)
        self.assertTrue(ok)
        self.assertIn(name, exported)

    def test_empty_winner_export_returns_header_only(self):
        ok, exported = backend_app.export_winners("csv", self.db_path)
        self.assertTrue(ok)
        self.assertEqual(exported.splitlines(), ["round,number,name,phone,created_at"])

    def test_draw_after_reset_without_new_participants_fails(self):
        self.register("张三")
        self.reset({"confirm": True})
        result = self.draw()
        self.assertFalse(result["success"])
        self.assertIn(result["error"]["code"], {"NO_PARTICIPANTS", "NO_AVAILABLE_PARTICIPANTS"})


class WsgiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = os.environ.get("LOTTERY_DB")
        os.environ["LOTTERY_DB"] = str(Path(self.tmp.name) / "lottery.db")
        backend_app.DATABASE_PATH = Path(os.environ["LOTTERY_DB"])
        backend_app.init_db()

    def tearDown(self) -> None:
        if self.original_db is None:
            os.environ.pop("LOTTERY_DB", None)
        else:
            os.environ["LOTTERY_DB"] = self.original_db
        backend_app.DATABASE_PATH = Path(os.environ.get("LOTTERY_DB", backend_app.PROJECT_ROOT / "lottery.db"))
        self.tmp.cleanup()

    def request(self, method, path, body=None, query_string="", cookie=""):
        encoded = b""
        if body is not None:
            if isinstance(body, bytes):
                encoded = body
            else:
                encoded = json.dumps(body).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query_string,
            "CONTENT_LENGTH": str(len(encoded)),
            "wsgi.input": io.BytesIO(encoded),
            "HTTP_COOKIE": cookie,
        }
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        response_body = b"".join(backend_app.app(environ, start_response))
        return captured["status"], captured["headers"], response_body

    def test_backend_serves_frontend_files(self):
        status, headers, body = self.request("GET", "/home/login")
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("访问验证".encode("utf-8"), body)

        cookie = f"{backend_app.HOME_AUTH_COOKIE}={backend_app.HOME_AUTH_TOKEN}"

        status, headers, body = self.request("GET", "/home", cookie=cookie)
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("参与抽奖".encode("utf-8"), body)

        status, headers, body = self.request("GET", "/home/join", cookie=cookie)
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("/lottery.png".encode("utf-8"), body)

        status, headers, body = self.request("GET", "/home/draw", cookie=cookie)
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("开始抽奖".encode("utf-8"), body)

        status, headers, body = self.request("GET", "/register")
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("扫码取号".encode("utf-8"), body)

        status, _, _ = self.request("GET", "/draw")
        self.assertEqual(status, "404 Not Found")

        status, headers, body = self.request("GET", "/static/register.js")
        self.assertEqual(status, "200 OK")
        self.assertIn("/api/register", body.decode("utf-8"))

    def test_home_pages_require_password(self):
        status, headers, _ = self.request("GET", "/home/draw")
        self.assertEqual(status, "302 Found")
        self.assertTrue(headers["Location"].startswith("/home/login"))

        form = b"password=123456"
        status, headers, _ = self.request("POST", "/home/login", form, query_string="next=%2Fhome%2Fdraw")
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/home/draw")
        self.assertIn(f"{backend_app.HOME_AUTH_COOKIE}=", headers["Set-Cookie"])

        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, body = self.request("GET", "/home/draw", cookie=cookie)
        self.assertEqual(status, "200 OK")
        self.assertIn("开始抽奖".encode("utf-8"), body)

    def test_home_logout_returns_to_login_page(self):
        cookie = f"{backend_app.HOME_AUTH_COOKIE}={backend_app.HOME_AUTH_TOKEN}"
        status, headers, _ = self.request("POST", "/home/logout", cookie=cookie)
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/home/login?logged_out=1")
        self.assertIn("Max-Age=0", headers["Set-Cookie"])

    def test_admin_api_requires_home_password(self):
        status, _, body = self.request("GET", "/api/participants")
        result = json.loads(body.decode("utf-8"))
        self.assertEqual(status, "401 Unauthorized")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "UNAUTHORIZED")

    def test_frontend_api_endpoints_are_connected(self):
        cookie = f"{backend_app.HOME_AUTH_COOKIE}={backend_app.HOME_AUTH_TOKEN}"
        status, _, body = self.request("POST", "/api/register", {"name": "张三", "phone": "13800138000"})
        registered = json.loads(body.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertTrue(registered["success"])

        status, _, body = self.request("GET", "/api/participants", cookie=cookie)
        participants = json.loads(body.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertTrue(participants["success"])
        self.assertEqual(participants["data"]["participants"][0]["name"], "张三")

        status, _, body = self.request("POST", "/api/draw", {"count": 1, "exclude_winners": True}, cookie=cookie)
        drawn = json.loads(body.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertTrue(drawn["success"])
        self.assertEqual(drawn["data"]["winner"]["name"], "张三")

    def test_runtime_server_is_threaded_for_concurrent_requests(self):
        self.assertTrue(issubclass(backend_app.ThreadedWSGIServer, ThreadingMixIn))
        self.assertTrue(backend_app.ThreadedWSGIServer.daemon_threads)
        self.assertGreaterEqual(backend_app.ThreadedWSGIServer.request_queue_size, 200)


if __name__ == "__main__":
    unittest.main()
