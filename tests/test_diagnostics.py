"""0.2.12 B6 诊断上传：字段裁剪/大小/限频/匿名/清理测试。"""

import gzip
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    capture_logger,
    install_fastapi_stub,
    install_jwt_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()
install_jwt_stub()

from api import v1_common, v1_diagnostics  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from service import diagnostics as diagnostics_service  # noqa: E402
from service.messages import MessageRateLimiter  # noqa: E402


def batch(**overrides):
    payload = {
        "client_diag_id": "diag-uuid-1",
        "session_id": "session-uuid-1",
        "client_version": "0.2.12",
        "events": [
            {"kind": "env", "payload": {"os_version": "Windows 11", "secret": "x"}},
            {"kind": "tick", "payload": {"ts": 1, "phase": "R2", "drop_me": "x"}},
            {"kind": "event", "payload": {"share_on": True}},
        ],
    }
    payload.update(overrides)
    return payload


class FakeRequest:
    def __init__(self, body=b"", headers=None):
        self._body = body
        self.headers = headers or {}
        self.state = SimpleNamespace(request_id="req_diag")
        self.url = SimpleNamespace(path="/api/v1/diagnostics/batch")
        self.method = "POST"
        self.client = SimpleNamespace(host="203.0.113.10")

    async def body(self):
        return self._body


class DiagnosticsValidationTests(unittest.TestCase):
    def test_sanitize_whitelist_and_truncate(self):
        meta, events, skipped = diagnostics_service.validate_batch(batch())
        self.assertEqual(0, skipped)
        env_payload = events[0]["payload"]
        self.assertNotIn("secret", env_payload)
        self.assertNotIn("drop_me", events[1]["payload"])
        self.assertEqual("Windows 11", env_payload["os_version"])

    def test_long_string_and_list_trimmed(self):
        payload = batch(
            events=[
                {
                    "kind": "tick",
                    "payload": {
                        "phase": "x" * 1000,
                        "start_ocr": {
                            "lines": [{"text": "y" * 1000}] * 200
                        },
                    },
                }
            ]
        )
        _, events, _ = diagnostics_service.validate_batch(payload)
        self.assertEqual(512, len(events[0]["payload"]["phase"]))
        self.assertEqual(100, len(events[0]["payload"]["start_ocr"]["lines"]))

    def test_settings_sensitive_keys_and_unknown_dropped(self):
        _, events, _ = diagnostics_service.validate_batch(
            batch(
                events=[
                    {
                        "kind": "event",
                        "payload": {
                            "settings": {
                                "auto_start": True,
                                "overlay_enabled": False,
                                "password": "p@ss",
                                "jwt": "xxx",
                                "api_key": "k",
                                "apiKey": "k2",
                                "nested": {"secret": "s", "token": "t"},
                                "unknown": 1,
                                "interval": 800,
                            }
                        },
                    }
                ]
            )
        )
        settings = events[0]["payload"]["settings"]
        self.assertEqual(
            {"auto_start": True, "overlay_enabled": False, "interval": 800},
            settings,
        )

    def test_nested_sensitive_keys_removed(self):
        _, events, _ = diagnostics_service.validate_batch(
            batch(
                events=[
                    {
                        "kind": "env",
                        "payload": {
                            "game_window": {
                                "title": "Delta",
                                "password": "x",
                                "nested": {"jwt": "y", "ok": 1},
                            }
                        },
                    }
                ]
            )
        )
        game = events[0]["payload"]["game_window"]
        self.assertEqual({"title": "Delta"}, {k: v for k, v in game.items() if k in ("title",)})
        self.assertNotIn("password", game)
        self.assertNotIn("nested", game)

    def test_invalid_kind_skipped_and_empty_rejected(self):
        _, events, skipped = diagnostics_service.validate_batch(
            batch(events=[{"kind": "bad", "payload": {}}, {"kind": "tick", "payload": {"ts": 1}}])
        )
        self.assertEqual(1, skipped)
        self.assertEqual("tick", events[0]["kind"])
        with self.assertRaises(diagnostics_service.DiagnosticError):
            diagnostics_service.validate_batch(batch(events=[{"kind": "bad", "payload": {}}]))

    def test_too_many_events(self):
        with self.assertRaises(diagnostics_service.DiagnosticError) as ctx:
            diagnostics_service.validate_batch(
                batch(events=[{"kind": "tick", "payload": {"ts": 1}}] * 1001)
            )
        self.assertEqual("DIAGNOSTICS_TOO_LARGE", ctx.exception.error_code)


class DiagnosticsApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.save = mock.AsyncMock(return_value=3)
        self.patcher = mock.patch.object(diagnostics_service, "save_batch", self.save)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.limiter = MessageRateLimiter(max_messages=6, window_seconds=60)
        limiter_patcher = mock.patch.object(
            v1_diagnostics.diagnostics_service, "rate_limiter", self.limiter
        )
        limiter_patcher.start()
        self.addCleanup(limiter_patcher.stop)

    async def test_anonymous_batch_accepted_and_fields_trimmed(self):
        request = FakeRequest(
            body=json.dumps(batch()).encode(),
            headers={"X-Diag-Id": "diag-uuid-1"},
        )
        response = await v1_diagnostics.upload_diagnostics(request, user=None)
        self.assertEqual(3, response["data"]["accepted"])
        self.assertEqual(0, response["data"]["skipped"])
        args = self.save.await_args.args
        self.assertIsNone(args[2])
        self.assertNotIn("secret", json.dumps(args[1], ensure_ascii=False))

    async def test_settings_secrets_not_passed_to_storage(self):
        payload = batch(
            events=[
                {
                    "kind": "event",
                    "payload": {
                        "settings": {
                            "auto_start": True,
                            "password": "p@ss",
                            "jwt": "token",
                            "api_key": "key",
                            "nested": {"secret": "x"},
                        },
                        "password": "top-level",
                    },
                }
            ]
        )
        response = await v1_diagnostics.upload_diagnostics(
            FakeRequest(body=json.dumps(payload).encode()), user=None
        )
        self.assertEqual(3, response["data"]["accepted"])
        saved = self.save.await_args.args
        stored = json.dumps(saved[1], ensure_ascii=False)
        self.assertIn("auto_start", stored)
        for secret in ("p@ss", "token", "api_key", "secret", "top-level"):
            self.assertNotIn(secret, stored)

    async def test_authenticated_has_account_id(self):
        request = FakeRequest(
            body=json.dumps(batch()).encode(),
            headers={"X-Diag-Id": "diag-uuid-1"},
        )
        await v1_diagnostics.upload_diagnostics(
            request, user=SimpleNamespace(id=12)
        )
        self.assertEqual(12, self.save.await_args.args[2])

    async def test_body_too_large(self):
        request = FakeRequest(body=b"x" * (512 * 1024 + 1))
        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_diagnostics.upload_diagnostics(request, user=None)
        self.assertEqual(413, ctx.exception.status_code)
        self.assertEqual("DIAGNOSTICS_TOO_LARGE", ctx.exception.error_code)

    async def test_gzip_compressed_too_large(self):
        request = FakeRequest(
            body=b"x" * (128 * 1024 + 1),
            headers={"Content-Encoding": "gzip"},
        )
        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_diagnostics.upload_diagnostics(request, user=None)
        self.assertEqual(413, ctx.exception.status_code)

    async def test_gzip_body_accepted(self):
        request = FakeRequest(
            body=gzip.compress(json.dumps(batch()).encode()),
            headers={"Content-Encoding": "gzip", "X-Diag-Id": "diag-uuid-1"},
        )
        response = await v1_diagnostics.upload_diagnostics(request, user=None)
        self.assertEqual(3, response["data"]["accepted"])

    async def test_rate_limit(self):
        limiter = MessageRateLimiter(max_messages=2, window_seconds=60)
        with mock.patch.object(
            v1_diagnostics.diagnostics_service, "rate_limiter", limiter
        ):
            for _ in range(2):
                await v1_diagnostics.upload_diagnostics(
                    FakeRequest(body=json.dumps(batch()).encode()), user=None
                )
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_diagnostics.upload_diagnostics(
                    FakeRequest(body=json.dumps(batch()).encode()), user=None
                )
        self.assertEqual(429, ctx.exception.status_code)
        self.assertEqual("DIAGNOSTICS_RATE_LIMITED", ctx.exception.error_code)

    async def test_db_failure_does_not_fail_request(self):
        self.save.side_effect = RuntimeError("db down")
        response = await v1_diagnostics.upload_diagnostics(
            FakeRequest(body=json.dumps(batch()).encode()), user=None
        )
        self.assertEqual(0, response["data"]["accepted"])


class DiagnosticsMigrationTests(unittest.TestCase):
    def test_migration_creates_diagnostics_table_idempotently(self):
        from scripts import migrate_f

        class Cursor:
            def __init__(self, tables):
                self.sql = []
                self.rowcount = 0
                self.tables = set(tables)

            def execute(self, sql, params=None):
                if sql.startswith("SELECT 1 FROM"):
                    table = sql.split()[3]
                    if table not in self.tables:
                        raise RuntimeError(f"no such table: {table}")
                self.sql.append((sql, params))

            def fetchone(self):
                return (0,)

            def fetchall(self):
                return []

            def close(self):
                pass

        class Connection:
            def __init__(self, tables):
                self.cursor_obj = Cursor(tables)
                self.tables = set(tables)

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                pass

            def rollback(self):
                pass

        conn = Connection(tables=("users", "rooms", "room_skill_states"))
        stats = migrate_f.migrate(conn)
        self.assertEqual(1, stats["diagnostics_table_created"])
        self.assertTrue(
            any("CREATE TABLE IF NOT EXISTS diagnostics_events" in sql for sql, _ in conn.cursor_obj.sql)
        )
        # 已存在后再次执行不应重复建表
        conn2 = Connection(
            tables=("users", "rooms", "room_skill_states", "diagnostics_events")
        )
        stats2 = migrate_f.migrate(conn2)
        self.assertEqual(0, stats2["diagnostics_table_created"])
        self.assertFalse(
            any("CREATE TABLE IF NOT EXISTS diagnostics_events" in sql for sql, _ in conn2.cursor_obj.sql)
        )


class DiagnosticsOpsTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_cutoff_7_days(self):
        with mock.patch.object(
            diagnostics_service.DiagnosticEventDao,
            "delete_before",
            new=mock.AsyncMock(return_value=5),
        ) as delete:
            now = datetime(2026, 10, 24, 12, 0, 0)
            self.assertEqual(5, await diagnostics_service.cleanup_old_events(now=now))
        cutoff = delete.await_args.args[0]
        self.assertEqual(7, (now - cutoff).days)

    def test_error_log_has_account_and_diag_id(self):
        capture = capture_logger(service_logger)
        handler = capture.__enter__()
        try:
            request = SimpleNamespace(
                headers={"X-Diag-Id": "diag-123"},
                state=SimpleNamespace(account_user_id="12"),
                url=SimpleNamespace(path="/api/v1/test"),
            )
            import asyncio

            asyncio.run(
                v1_common.api_error_handler(
                    request,
                    v1_common.APIError(400, 40000, "bad", error_code="TEST"),
                )
            )
        finally:
            capture.__exit__(None, None, None)
        text = "\n".join(handler.messages())
        self.assertIn("account_user_id=12", text)
        self.assertIn("client_diag_id=diag-123", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
