"""B2b：WebSocket JWT 认证/顶号/未认证拒绝测试（离线）。"""

import json
import os
import time
import unittest
from unittest import mock

from tests.support import capture_logger, install_jwt_stub
from tests.ws_support import FakeWebSocket, make_user, manager_module

install_jwt_stub()

from log.logger import logger as service_logger  # noqa: E402
from security import tokens  # noqa: E402

SECRET = "unit-test-jwt-secret-0123456789abcdef"


class WsAuthTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": SECRET, "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        tokens._weak_secret_warned = False
        self.manager = manager_module.WebSocketManager()
        self.websocket = FakeWebSocket()
        self.session = manager_module.ClientSession(websocket=self.websocket)

    async def authenticate_user(self, user=None, websocket=None, token=None):
        user = user or make_user()
        websocket = websocket or self.websocket
        if token is None:
            token, _ = tokens.create_access_token(user.id)
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ):
            ok = await self.manager.authenticate(
                manager_module.ClientSession(websocket=websocket), token
            )
        return ok


class UnauthenticatedTests(WsAuthTestCase):
    async def test_non_auth_message_is_rejected(self):
        await self.manager.process_message(
            self.session, json.dumps({"type": "join_room", "room_id_or_code": "A1B2C"})
        )
        self.assertEqual("NOT_AUTHENTICATED", self.websocket.last()["code"])
        self.assertIsNone(self.websocket.closed)
        self.assertFalse(self.session.authenticated)

    async def test_malformed_json_returns_error_without_crash(self):
        for raw in ("{not json", "[]", b"\xff"):
            with self.subTest(raw=raw):
                self.websocket.sent.clear()
                await self.manager.process_message(self.session, raw)
                self.assertEqual("error", self.websocket.last()["type"])
        self.assertFalse(self.session.authenticated)

    async def test_legacy_authenticate_is_removed(self):
        await self.manager.process_message(
            self.session, json.dumps({"type": "authenticate", "username": "alice"})
        )
        self.assertEqual("auth_error", self.websocket.last()["type"])
        self.assertEqual("AUTH_INVALID", self.websocket.last()["code"])
        self.assertEqual(manager_module.WS_CLOSE_AUTH_FAILED, self.websocket.closed[0])


class AuthSuccessTests(WsAuthTestCase):
    async def test_auth_success_registers_session(self):
        user = make_user(7, "bob")
        token, _ = tokens.create_access_token(user.id)
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ) as get_user:
            ok = await self.manager.authenticate(self.session, token)

        self.assertTrue(ok)
        get_user.assert_awaited_once_with(7)
        response = self.websocket.last()
        self.assertEqual("auth_ok", response["type"])
        self.assertEqual({"id": "7", "username": "bob"}, response["user"])
        self.assertIs(self.session, self.manager.sessions["7"])
        self.assertTrue(self.session.authenticated)
        self.assertEqual("bob", self.session.user.username)

    async def test_auth_twice_is_rejected(self):
        user = make_user()
        token, _ = tokens.create_access_token(user.id)
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ):
            await self.manager.authenticate(self.session, token)
            self.websocket.sent.clear()
            await self.manager.authenticate(self.session, token)
        self.assertEqual("error", self.websocket.last()["type"])
        self.assertEqual("ALREADY_AUTHENTICATED", self.websocket.last()["code"])

    async def test_missing_user_returns_auth_invalid(self):
        token, _ = tokens.create_access_token(99)
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=None),
        ):
            ok = await self.manager.authenticate(self.session, token)
        self.assertFalse(ok)
        self.assertEqual("AUTH_INVALID", self.websocket.last()["code"])
        self.assertEqual(manager_module.WS_CLOSE_AUTH_FAILED, self.websocket.closed[0])


class AuthFailureTests(WsAuthTestCase):
    async def test_invalid_token_returns_auth_invalid_and_closes(self):
        ok = await self.manager.authenticate(self.session, "not-a-token")
        self.assertFalse(ok)
        response = self.websocket.last()
        self.assertEqual("auth_error", response["type"])
        self.assertEqual("AUTH_INVALID", response["code"])
        self.assertEqual(manager_module.WS_CLOSE_AUTH_FAILED, self.websocket.closed[0])
        self.assertEqual({}, self.manager.sessions)

    async def test_expired_token_returns_auth_expired(self):
        token, _ = tokens.create_access_token(
            make_user().id, now=time.time() - 10 * 24 * 3600
        )
        ok = await self.manager.authenticate(self.session, token)
        self.assertFalse(ok)
        self.assertEqual("AUTH_EXPIRED", self.websocket.last()["code"])
        self.assertEqual(manager_module.WS_CLOSE_AUTH_FAILED, self.websocket.closed[0])

    async def test_missing_jwt_secret_returns_auth_invalid(self):
        token, _ = tokens.create_access_token(make_user().id)
        with mock.patch.dict(os.environ, {}, clear=False), capture_logger(service_logger):
            os.environ.pop("JWT_SECRET", None)
            ok = await self.manager.authenticate(self.session, token)
        self.assertFalse(ok)
        self.assertEqual("AUTH_INVALID", self.websocket.last()["code"])


class SingleConnectionTests(WsAuthTestCase):
    async def test_new_connection_replaces_old_one(self):
        user = make_user()
        token, _ = tokens.create_access_token(user.id)

        old_websocket = FakeWebSocket()
        old_session = manager_module.ClientSession(websocket=old_websocket)
        new_session = manager_module.ClientSession(websocket=self.websocket)
        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ):
            self.assertTrue(await self.manager.authenticate(old_session, token))
            self.assertTrue(await self.manager.authenticate(new_session, token))

        # 旧连接被关闭且已从 sessions 移除，新连接成为唯一映射
        self.assertEqual(manager_module.WS_CLOSE_REPLACED, old_websocket.closed[0])
        self.assertIs(new_session, self.manager.sessions[str(user.id)])
        self.assertEqual(1, len(self.manager.sessions))

    async def test_replaced_connection_leaves_room(self):
        user = make_user()
        token, _ = tokens.create_access_token(user.id)
        old_session = manager_module.ClientSession(websocket=FakeWebSocket())
        old_session.user = user
        old_session.user_id = str(user.id)
        old_session.room_id = "room-1"
        old_session.room_code = "A1B2C"
        self.manager.sessions[str(user.id)] = old_session

        observer_ws = FakeWebSocket()
        observer = manager_module.ClientSession(websocket=observer_ws)
        observer.user = make_user(2, "bob")
        observer.user_id = "2"
        observer.room_id = "room-1"
        observer.room_code = "A1B2C"
        self.manager.sessions["2"] = observer

        with mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=user),
        ):
            await self.manager.authenticate(
                manager_module.ClientSession(websocket=self.websocket), token
            )

        user_left = [m for m in observer_ws.messages("user_left")]
        self.assertEqual(1, len(user_left))
        self.assertEqual("replaced", user_left[0]["reason"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
