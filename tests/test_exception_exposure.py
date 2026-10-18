"""A2）补齐异常原文回传路径：room / user 映射与 SQL error 字段脱敏。

覆盖：
- api/room.py 未知异常 → 通用 error_code/message/request_id，异常详情只进日志
- api/room.py 已知业务异常的错误码/状态码映射不受影响
- service/user.py 数据库异常 → 对外通用文案，详情只进日志
- api/user.py 未登记的服务层 message → 回退通用文案，不透传异常原文
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
from api import user as user_api  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from service import user as user_service  # noqa: E402
from service.room import (  # noqa: E402
    InvalidDomainError,
    RoomAlreadyExistsError,
    RoomNotFoundError,
    RoomServiceError,
    UserNotInRoomError,
)

SECRET = "mysql://root:Pa55w0rd!@114.132.161.169:3306/delta_service"


class _DummyURL:
    path = "/api/room/create"


class _DummyClient:
    host = "203.0.113.10"


class _DummyState:
    pass


class DummyRequest:
    """只实现 handle_service_exception / 路由实际访问到的 Request 属性。"""

    def __init__(self, request_id=None):
        self.url = _DummyURL()
        self.method = "POST"
        self.client = _DummyClient()
        self.state = _DummyState()
        if request_id is not None:
            self.state.request_id = request_id


class RoomExceptionMappingTests(unittest.TestCase):
    def test_unknown_exception_returns_generic_error_and_logs_details(self):
        request = DummyRequest(request_id="req_room_unknown")
        with capture_logger(service_logger) as capture:
            http_exc = room_api.handle_service_exception(
                RuntimeError(f"boom {SECRET}"), request
            )

        self.assertEqual(500, http_exc.status_code)
        self.assertEqual(
            {
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
                "request_id": "req_room_unknown",
            },
            http_exc.detail,
        )

        body_text = json.dumps(http_exc.detail, ensure_ascii=False)
        for leaked in ("Pa55w0rd!", "114.132.161.169", "mysql://", "boom"):
            self.assertNotIn(leaked, body_text)

        # 完整异常（type/message）只写服务端日志，且带 request_id 可检索
        log_text = "\n".join(capture.messages())
        self.assertIn("RuntimeError", log_text)
        self.assertIn("boom", log_text)
        self.assertIn("request_id:req_room_unknown", log_text)
        self.assertIn("Pa55w0rd!", log_text)

    def test_unknown_exception_without_request_generates_request_id(self):
        with capture_logger(service_logger):
            http_exc = room_api.handle_service_exception(RuntimeError("no request"))
        self.assertEqual(500, http_exc.status_code)
        self.assertTrue(http_exc.detail["request_id"].startswith("req_"))
        self.assertNotIn("detail", http_exc.detail)

    def test_known_exceptions_keep_error_code_and_status(self):
        cases = [
            (RoomNotFoundError("ABC12"), 404, "ROOM_NOT_FOUND"),
            (RoomAlreadyExistsError("ABC12"), 409, "ROOM_ALREADY_EXISTS"),
            (InvalidDomainError(9), 400, "INVALID_DOMAIN"),
            (UserNotInRoomError("u1", "ABC12"), 400, "USER_NOT_IN_ROOM"),
            (RoomServiceError("内部业务错误", "SOME_CODE"), 500, "SOME_CODE"),
        ]
        for exc, expected_status, expected_code in cases:
            with self.subTest(error_code=expected_code):
                http_exc = room_api.handle_service_exception(exc)
                self.assertEqual(expected_status, http_exc.status_code)
                self.assertEqual(expected_code, http_exc.detail["error_code"])
                self.assertEqual(exc.message, http_exc.detail["message"])
                self.assertNotIn("detail", http_exc.detail)


class RoomRouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_room_api_unknown_error_returns_no_exception_text(self):
        request = DummyRequest(request_id="req_room_route")
        payload = SimpleNamespace(user_id="u1", domain_enum=1)

        with mock.patch.object(
            room_api, "create_room", side_effect=RuntimeError(f"db down {SECRET}")
        ), capture_logger(service_logger) as capture:
            with self.assertRaises(room_api.HTTPException) as ctx:
                await room_api.create_room_api(request, payload)

        http_exc = ctx.exception
        self.assertEqual(500, http_exc.status_code)
        self.assertEqual("INTERNAL_ERROR", http_exc.detail["error_code"])
        self.assertEqual("req_room_route", http_exc.detail["request_id"])
        self.assertNotIn(
            "Pa55w0rd!", json.dumps(http_exc.detail, ensure_ascii=False)
        )
        self.assertNotIn(
            "114.132.161.169", json.dumps(http_exc.detail, ensure_ascii=False)
        )
        self.assertIn("request_id:req_room_route", "\n".join(capture.messages()))


class UserServiceErrorMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_user_db_error_hides_details(self):
        secret = "Duplicate entry 'alice' for key 'username' Pa55w0rd!"
        with mock.patch.object(
            user_service.UserDao, "create_user", new=mock.AsyncMock(side_effect=RuntimeError(secret))
        ), capture_logger(service_logger) as capture:
            result = await user_service.UserService.create_user("alice", "Pa55w0rd!")

        self.assertFalse(result["success"])
        self.assertEqual("创建用户失败", result["message"])
        self.assertNotIn("Duplicate", result["message"])
        self.assertNotIn("Pa55w0rd!", result["message"])
        self.assertTrue(any(secret in message for message in capture.messages()))

    async def test_login_db_error_hides_details(self):
        secret = "connect failed mysql://root:Pa55w0rd!@host:3306/db"
        with mock.patch.object(
            user_service.UserDao, "login", new=mock.AsyncMock(side_effect=RuntimeError(secret))
        ), capture_logger(service_logger) as capture:
            result = await user_service.UserService.login("alice", "Pa55w0rd!")

        self.assertFalse(result["success"])
        self.assertEqual("登录失败", result["message"])
        self.assertNotIn("mysql://", result["message"])
        self.assertTrue(any(secret in message for message in capture.messages()))

    async def test_list_users_db_error_hides_details(self):
        with mock.patch.object(
            user_service.UserDao,
            "list_users",
            new=mock.AsyncMock(side_effect=RuntimeError("SELECT ... password=Pa55w0rd!")),
        ) as mocked, capture_logger(service_logger) as capture:
            result = await user_service.UserService.list_users(0, 10)

        self.assertFalse(result["success"])
        self.assertEqual("获取用户列表失败", result["message"])
        self.assertNotIn("password", result["message"])
        self.assertTrue(capture.records)

    async def test_success_message_unchanged(self):
        fake_user = SimpleNamespace(id=1, username="alice")
        with mock.patch.object(
            user_service.UserDao, "create_user", new=mock.AsyncMock(return_value=fake_user)
        ), capture_logger(service_logger):
            result = await user_service.UserService.create_user("alice", "Pa55w0rd!")

        self.assertTrue(result["success"])
        self.assertEqual("用户创建成功", result["message"])
        self.assertEqual({"id": 1, "username": "alice"}, result["user"])


class UserApiErrorDetailTests(unittest.IsolatedAsyncioTestCase):
    async def test_unregistered_service_message_falls_back_to_generic(self):
        fake_result = {
            "success": False,
            "message": "创建用户失败: Duplicate entry 'alice' for key 'username'",
        }
        with mock.patch.object(
            user_api.UserService, "create_user", new=mock.AsyncMock(return_value=fake_result)
        ):
            with self.assertRaises(user_api.HTTPException) as ctx:
                await user_api.create_user(
                    SimpleNamespace(username="alice", password="Pa55w0rd!")
                )

        http_exc = ctx.exception
        self.assertEqual(400, http_exc.status_code)
        self.assertEqual("创建用户失败", http_exc.detail)
        self.assertNotIn("Duplicate", str(http_exc.detail))

    async def test_known_domain_message_passes_through(self):
        fake_result = {"success": False, "message": "用户不存在"}
        with mock.patch.object(
            user_api.UserService, "get_user_by_id", new=mock.AsyncMock(return_value=fake_result)
        ):
            with self.assertRaises(user_api.HTTPException) as ctx:
                await user_api.get_user_by_id(42)

        http_exc = ctx.exception
        self.assertEqual(404, http_exc.status_code)
        self.assertEqual("用户不存在", http_exc.detail)

    async def test_login_internal_message_falls_back_to_generic(self):
        fake_result = {
            "success": False,
            "message": "登录失败: connection reset by 114.132.161.169",
        }
        with mock.patch.object(
            user_api.UserService, "login", new=mock.AsyncMock(return_value=fake_result)
        ):
            with self.assertRaises(user_api.HTTPException) as ctx:
                await user_api.login(
                    SimpleNamespace(username="alice", password="Pa55w0rd!")
                )

        http_exc = ctx.exception
        self.assertEqual(401, http_exc.status_code)
        self.assertEqual("登录失败", http_exc.detail)
        self.assertNotIn("114.132.161.169", str(http_exc.detail))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
