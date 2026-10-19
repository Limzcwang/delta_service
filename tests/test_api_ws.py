"""B2b：FastAPI /ws 端点、单端口与 legacy 8765 开关测试（离线）。"""

import json
import os
import unittest
from unittest import mock

from tests.support import (
    REPO_ROOT,
    capture_logger,
    install_fastapi_stub,
    install_jwt_stub,
    install_pydantic_stub,
    install_tortoise_stub,
    load_main_module,
)
from tests.ws_support import FakeWebSocket, make_room, make_user, manager_module

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()
install_jwt_stub()

from api import ws as api_ws  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from security import tokens  # noqa: E402
from service.rooms import RoomError  # noqa: E402

SECRET = "unit-test-jwt-secret-0123456789abcdef"


class FakeFastAPIWebSocket:
    """模拟 FastAPI WebSocket 的 accept/receive_text/send_text/close。"""

    def __init__(self, incoming=None):
        self.incoming = list(incoming or [])
        self.sent = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise api_ws.WebSocketDisconnect()

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=None):
        self.closed = (code, reason)

    def messages(self, message_type=None):
        if message_type is None:
            return list(self.sent)
        return [item for item in self.sent if item.get("type") == message_type]


class ApiWsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": SECRET, "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.manager = manager_module.WebSocketManager()
        patcher = mock.patch.object(api_ws, "websocket_manager", self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)


class ApiWsEndpointTests(ApiWsTestCase):
    async def test_auth_over_fastapi_websocket(self):
        user = make_user()
        token, _ = tokens.create_access_token(user.id)
        fake_ws = FakeFastAPIWebSocket([json.dumps({"type": "auth", "token": token})])
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ):
            await api_ws.websocket_endpoint(fake_ws)

        self.assertTrue(fake_ws.accepted)
        auth_ok = fake_ws.messages("auth_ok")[0]
        self.assertEqual(
            {"id": "1", "username": "alice", "nickname": "alice"},
            auth_ok["user"],
        )
        self.assertEqual({}, self.manager.sessions)  # finally 已清理

    async def test_bad_auth_closes_connection(self):
        fake_ws = FakeFastAPIWebSocket([json.dumps({"type": "auth", "token": "bad"})])
        await api_ws.websocket_endpoint(fake_ws)

        self.assertEqual("auth_error", fake_ws.sent[0]["type"])
        self.assertEqual("AUTH_INVALID", fake_ws.sent[0]["code"])
        self.assertEqual(manager_module.WS_CLOSE_AUTH_FAILED, fake_ws.closed[0])

    async def test_join_room_over_fastapi_websocket(self):
        user = make_user()
        token, _ = tokens.create_access_token(user.id)
        room = make_room()
        members = [{"id": "1", "nickname": "alice", "role": "owner"}]
        fake_ws = FakeFastAPIWebSocket([
            json.dumps({"type": "auth", "token": token}),
            json.dumps({"type": "join_room", "room_id_or_code": room.code}),
        ])
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ), mock.patch.object(
            manager_module.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(room, members)),
        ):
            await api_ws.websocket_endpoint(fake_ws)

        room_joined = fake_ws.messages("room_joined")
        self.assertEqual(1, len(room_joined))
        self.assertEqual(room.id, room_joined[0]["room"]["id"])
        self.assertTrue(room_joined[0]["members"][0]["online"])

    async def test_non_member_join_returns_error(self):
        user = make_user()
        token, _ = tokens.create_access_token(user.id)
        fake_ws = FakeFastAPIWebSocket([
            json.dumps({"type": "auth", "token": token}),
            json.dumps({"type": "join_room", "room_id_or_code": "A1B2C"}),
        ])
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ), mock.patch.object(
            manager_module.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(
                side_effect=RoomError("NOT_ROOM_MEMBER", "你不在该房间中", 403)
            ),
        ):
            await api_ws.websocket_endpoint(fake_ws)

        errors = fake_ws.messages("error")
        self.assertEqual(1, len(errors))
        self.assertEqual("NOT_ROOM_MEMBER", errors[0]["code"])
        self.assertIsNone(fake_ws.closed)  # 房间错误不关闭连接


class RouterRegistrationTests(unittest.TestCase):
    def test_ws_route_registered(self):
        paths = [path for _method, path in api_ws.router.full_paths()]
        self.assertIn("/ws", paths)

    def test_create_app_includes_ws_router(self):
        source = (REPO_ROOT / "api" / "init.py").read_text(encoding="utf-8")
        self.assertIn("ws_router", source)


class LegacyWsSwitchTests(unittest.TestCase):
    def setUp(self):
        from tests.support import install_main_dependency_stubs

        install_main_dependency_stubs()
        self.main_module = load_main_module("main_ws_switch_test")

    def test_legacy_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEGACY_WS_ENABLED", None)
            self.assertFalse(self.main_module._legacy_ws_enabled())

    def test_legacy_enabled_values(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    os.environ, {"LEGACY_WS_ENABLED": value}, clear=False
                ):
                    self.assertTrue(self.main_module._legacy_ws_enabled())

    def test_legacy_disabled_values(self):
        for value in ("0", "false", "", "no", "off"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    os.environ, {"LEGACY_WS_ENABLED": value}, clear=False
                ):
                    self.assertFalse(self.main_module._legacy_ws_enabled())

    def test_main_source_only_starts_legacy_conditionally(self):
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("_legacy_ws_enabled()", source)
        self.assertIn("LEGACY_WS_ENABLED", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
