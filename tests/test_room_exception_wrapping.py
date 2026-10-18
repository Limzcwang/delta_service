"""A3）room 服务层不再把内部异常原文拼进客户端可见的 RoomServiceError.message。

覆盖 4 个客户端可见的 500 分支（CREATE_ROOM / JOIN_ROOM / LEAVE_ROOM /
GET_PUBLIC_ROOMS）：内部异常时响应只含通用文案 + 不变的 error_code，
原始异常（含连接串）只写服务端日志并带 request_id。
同时确认已知业务异常映射（404/400）语义未被改变。
"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    capture_logger,
    install_fastapi_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()

from api import room as room_api  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from service import room as room_service  # noqa: E402

SECRET = "mysql://root:Pa55w0rd!@114.132.161.169:3306/delta_service"
LEAK_MARKERS = ("Pa55w0rd!", "114.132.161.169", "mysql://", "Traceback", "RuntimeError")


class _DummyClient:
    host = "203.0.113.10"


class _DummyState:
    pass


class DummyRequest:
    def __init__(self, request_id=None, path="/api/room/create", method="POST"):
        self.url = SimpleNamespace(path=path)
        self.method = method
        self.client = _DummyClient()
        self.state = _DummyState()
        if request_id is not None:
            self.state.request_id = request_id


def _seed_room(room_code="ABC12", members=()):
    room_service.ROOM_MANAGER[room_code] = {
        "domain": "public",
        "creator": "creator",
        "members": set(members),
        "online": set(),
        "created_at": 0.0,
        "messages": [],
    }
    room_service.ROOM_PUBLIC.append(room_code)


class RoomWrappingIsolation(unittest.TestCase):
    def setUp(self):
        room_service.ROOM_MANAGER.clear()
        room_service.ROOM_PUBLIC.clear()

    def tearDown(self):
        room_service.ROOM_MANAGER.clear()
        room_service.ROOM_PUBLIC.clear()


class RoomServiceInternalErrorWrappingTests(RoomWrappingIsolation, unittest.IsolatedAsyncioTestCase):
    """4 个客户端可见分支：内部异常 → 通用文案 + error_code，原文只进日志。"""

    async def _assert_no_leak(self, http_exc, expected_error_code, expected_message, request_id, capture):
        self.assertEqual(500, http_exc.status_code)
        detail = http_exc.detail
        self.assertEqual(expected_error_code, detail["error_code"])
        self.assertEqual(expected_message, detail["message"])
        self.assertNotIn("detail", detail)

        body_text = json.dumps(detail, ensure_ascii=False)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, body_text)
        self.assertNotIn(SECRET, body_text)

        # 原始异常（含连接串）只写服务端日志，且带 request_id 可对账
        log_text = "\n".join(capture.messages())
        self.assertIn(SECRET, log_text)
        self.assertIn(f"request_id:{request_id}", log_text)

    async def test_create_room_internal_error_is_not_exposed(self):
        request = DummyRequest(request_id="req_a3_create")
        payload = SimpleNamespace(user_id="u1", domain_enum=1)
        with mock.patch.object(
            room_service, "generate_room_code", side_effect=RuntimeError(f"boom {SECRET}")
        ), capture_logger(service_logger) as capture:
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.create_room_api(request, payload)

        await self._assert_no_leak(
            ctx.exception, "CREATE_ROOM_FAILED", "创建房间失败", "req_a3_create", capture
        )

    async def test_join_room_internal_error_is_not_exposed(self):
        _seed_room("ABC12")
        request = DummyRequest(request_id="req_a3_join")
        payload = SimpleNamespace(user_id="u1", room_code="ABC12")
        with mock.patch.object(
            room_service, "log_business_event", side_effect=RuntimeError(f"boom {SECRET}")
        ), capture_logger(service_logger) as capture:
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.join_room_api(request, payload)

        await self._assert_no_leak(
            ctx.exception, "JOIN_ROOM_FAILED", "加入房间失败", "req_a3_join", capture
        )

    async def test_leave_room_internal_error_is_not_exposed(self):
        _seed_room("ABC12", members=("u1",))
        request = DummyRequest(request_id="req_a3_leave")
        payload = SimpleNamespace(user_id="u1", room_code="ABC12")
        with mock.patch.object(
            room_service, "log_business_event", side_effect=RuntimeError(f"boom {SECRET}")
        ), capture_logger(service_logger) as capture:
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.leave_room_api(request, payload)

        await self._assert_no_leak(
            ctx.exception, "LEAVE_ROOM_FAILED", "离开房间失败", "req_a3_leave", capture
        )

    async def test_get_public_room_list_internal_error_is_not_exposed(self):
        _seed_room("ABC12")
        request = DummyRequest(request_id="req_a3_public", path="/api/room/public/list", method="GET")
        with mock.patch.object(
            room_service, "log_business_event", side_effect=RuntimeError(f"boom {SECRET}")
        ), capture_logger(service_logger) as capture:
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.get_public_room_list_api(request)

        await self._assert_no_leak(
            ctx.exception,
            "GET_PUBLIC_ROOMS_FAILED",
            "获取公共房间列表失败",
            "req_a3_public",
            capture,
        )

    async def test_service_error_objects_keep_generic_message(self):
        # 直接验证服务层包装后的 message 不再含 str(e)
        with mock.patch.object(
            room_service, "generate_room_code", side_effect=RuntimeError(SECRET)
        ), capture_logger(service_logger):
            with self.assertRaises(room_service.RoomServiceError) as ctx:
                room_service.create_room("u1", request_id="req_a3_service")
        self.assertEqual("CREATE_ROOM_FAILED", ctx.exception.error_code)
        self.assertEqual("创建房间失败", ctx.exception.message)
        self.assertNotIn("Pa55w0rd!", ctx.exception.message)


class RoomKnownExceptionSemanticsTests(RoomWrappingIsolation, unittest.IsolatedAsyncioTestCase):
    """已知业务异常映射不受影响（404/400）。"""

    async def test_join_missing_room_still_returns_room_not_found(self):
        request = DummyRequest(request_id="req_a3_known", path="/api/room/join")
        payload = SimpleNamespace(user_id="u1", room_code="NOPE")
        with capture_logger(service_logger):
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.join_room_api(request, payload)

        self.assertEqual(404, ctx.exception.status_code)
        self.assertEqual("ROOM_NOT_FOUND", ctx.exception.detail["error_code"])
        self.assertEqual("房间 NOPE 不存在", ctx.exception.detail["message"])

    async def test_leave_room_user_not_in_room_still_returns_400(self):
        _seed_room("ABC12", members=())
        request = DummyRequest(request_id="req_a3_known_leave", path="/api/room/leave")
        payload = SimpleNamespace(user_id="u2", room_code="ABC12")
        with capture_logger(service_logger):
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.leave_room_api(request, payload)

        self.assertEqual(400, ctx.exception.status_code)
        self.assertEqual("USER_NOT_IN_ROOM", ctx.exception.detail["error_code"])
        self.assertEqual("用户 u2 不在房间 ABC12", ctx.exception.detail["message"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
