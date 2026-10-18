"""B）全局异常处理器测试：异常详情不外传，完整异常只写服务端日志。"""

import json
import logging
import unittest

from tests.support import (
    capture_logger,
    install_main_dependency_stubs,
    load_main_module,
)

# 先装好 main.py 依赖的最小替身，保证离线（无数据库/网络）可导入。
install_main_dependency_stubs()
main = load_main_module()

from log.logger import logger as service_logger  # noqa: E402


class _DummyURL:
    path = "/api/user/login"


class _DummyClient:
    host = "203.0.113.10"


class _DummyState:
    pass


class DummyRequest:
    """只实现 global_exception_handler 实际访问到的 Request 属性。"""

    def __init__(self, request_id=None):
        self.url = _DummyURL()
        self.method = "POST"
        self.client = _DummyClient()
        self.state = _DummyState()
        if request_id is not None:
            self.state.request_id = request_id


class GlobalExceptionHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ctx = capture_logger(service_logger)
        self.capture = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    async def _handle(self, request, exc):
        return await main.global_exception_handler(request, exc)

    async def test_response_contains_only_generic_error_fields(self):
        secret_text = (
            "mysql://root:Pa55w0rd!@114.132.161.169:3306/delta_service 连接失败"
        )
        exc = RuntimeError(secret_text)

        response = await self._handle(DummyRequest(), exc)

        self.assertEqual(500, response.status_code)
        body_text = response.body.decode("utf-8")
        payload = json.loads(body_text)

        self.assertEqual(500, payload["code"])
        self.assertEqual("服务器内部错误", payload["message"])
        self.assertEqual(
            {"code", "message", "request_id"}, set(payload.keys())
        )
        self.assertNotIn("detail", payload)
        self.assertTrue(payload["request_id"].startswith("req_"))

    async def test_response_body_does_not_leak_exception_details(self):
        exc = RuntimeError("boom-marker Pa55w0rd! /srv/app/config.py")
        response = await self._handle(DummyRequest(), exc)
        body_text = response.body.decode("utf-8")

        self.assertNotIn("boom-marker", body_text)
        self.assertNotIn("Pa55w0rd!", body_text)
        self.assertNotIn("/srv/app/config.py", body_text)
        self.assertNotIn("RuntimeError", body_text)
        self.assertNotIn("Traceback", body_text)

    async def test_full_exception_written_to_server_log_only(self):
        try:
            raise ValueError("boom-marker: secret=Pa55w0rd!")
        except ValueError as exc:
            # 与 Starlette 的真实调用方式一致：异常处理器在 except 上下文中被调用，
            # 因此服务端日志能拿到完整 traceback。
            response = await self._handle(DummyRequest(), exc)

        self.assertEqual(1, len(self.capture.records))
        record = self.capture.records[0]
        message = record.getMessage()

        # 服务端日志包含异常类型、消息与 request_id，可检索对账
        self.assertEqual("ERROR", record.levelname)
        self.assertIn("ValueError", message)
        self.assertIn("boom-marker", message)
        self.assertIn("request_id:", message)

        request_id = json.loads(response.body.decode("utf-8"))["request_id"]
        self.assertIn(f"request_id:{request_id}", message)

        # 完整 traceback 只写日志
        self.assertIsNotNone(record.exc_info)
        traceback_text = logging.Formatter().formatException(record.exc_info)
        self.assertIn("Traceback", traceback_text)
        self.assertIn("ValueError", traceback_text)
        self.assertIn("boom-marker", traceback_text)

    async def test_reuses_request_id_from_request_state(self):
        request = DummyRequest(request_id="req_existing_123")
        response = await self._handle(request, RuntimeError("x"))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual("req_existing_123", payload["request_id"])
        self.assertIn("request_id:req_existing_123", self.capture.records[0].getMessage())

    async def test_generates_distinct_request_ids_when_missing(self):
        first = await self._handle(DummyRequest(), RuntimeError("first"))
        second = await self._handle(DummyRequest(), RuntimeError("second"))

        first_id = json.loads(first.body.decode("utf-8"))["request_id"]
        second_id = json.loads(second.body.decode("utf-8"))["request_id"]

        self.assertTrue(first_id.startswith("req_"))
        self.assertGreater(len(first_id), len("req_"))
        self.assertNotEqual(first_id, second_id)
        self.assertIn(f"request_id:{first_id}", self.capture.records[0].getMessage())
        self.assertIn(f"request_id:{second_id}", self.capture.records[1].getMessage())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
