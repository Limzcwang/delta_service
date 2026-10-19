"""B1：JWT 依赖注入、v1 登录与 /me、统一响应包测试。"""

import json
import os
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    REPO_ROOT,
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

from api import deps as auth_deps  # noqa: E402
from api import v1_auth, v1_common  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from security import tokens  # noqa: E402

SECRET = "unit-test-jwt-secret-0123456789abcdef"


class DummyRequest:
    def __init__(self, headers=None, request_id=None, ip="203.0.113.10"):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=ip)
        self.state = SimpleNamespace()
        self.url = SimpleNamespace(path="/api/v1/test")
        self.method = "POST"
        if request_id is not None:
            self.state.request_id = request_id


def _body(response):
    return json.loads(response.body.decode("utf-8"))


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": SECRET, "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class GetCurrentUserTests(AuthTestCase, unittest.IsolatedAsyncioTestCase):
    async def _call(self, request):
        return await auth_deps.get_current_user(request)

    async def test_missing_authorization_returns_40100(self):
        with self.assertRaises(v1_common.APIError) as ctx:
            await self._call(DummyRequest())
        self.assertEqual(401, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_UNAUTHORIZED, ctx.exception.code)

    async def test_malformed_authorization_returns_40100(self):
        for header in ("Token abc", "Bearer", "Bearer   ", "abc"):
            with self.subTest(header=header):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await self._call(DummyRequest(headers={"Authorization": header}))
                self.assertEqual(401, ctx.exception.status_code)
                self.assertEqual(v1_common.CODE_UNAUTHORIZED, ctx.exception.code)

    async def test_invalid_token_returns_40102(self):
        with mock.patch.object(
            auth_deps.UserDao, "get_user_by_id", new=mock.AsyncMock()
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await self._call(
                    DummyRequest(headers={"Authorization": "Bearer not-a-token"})
                )
        self.assertEqual(v1_common.CODE_TOKEN_INVALID, ctx.exception.code)

    async def test_expired_token_returns_40101(self):
        token, _ = tokens.create_access_token(1, now=time.time() - 10 * 24 * 3600)
        with self.assertRaises(v1_common.APIError) as ctx:
            await self._call(DummyRequest(headers={"Authorization": f"Bearer {token}"}))
        self.assertEqual(401, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_TOKEN_EXPIRED, ctx.exception.code)

    async def test_token_subject_must_be_numeric_user_id(self):
        token, _ = tokens.create_access_token("not-a-number")
        with self.assertRaises(v1_common.APIError) as ctx:
            await self._call(DummyRequest(headers={"Authorization": f"Bearer {token}"}))
        self.assertEqual(v1_common.CODE_TOKEN_INVALID, ctx.exception.code)

    async def test_deleted_or_missing_user_returns_40102(self):
        token, _ = tokens.create_access_token(42)
        with mock.patch.object(
            auth_deps.UserDao, "get_user_by_id", new=mock.AsyncMock(return_value=None)
        ) as get_user:
            with self.assertRaises(v1_common.APIError) as ctx:
                await self._call(DummyRequest(headers={"Authorization": f"Bearer {token}"}))
        get_user.assert_awaited_once_with(42)
        self.assertEqual(v1_common.CODE_TOKEN_INVALID, ctx.exception.code)

    async def test_valid_token_returns_user(self):
        token, _ = tokens.create_access_token(5)
        fake_user = SimpleNamespace(id=5, username="alice")
        with mock.patch.object(
            auth_deps.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(return_value=fake_user),
        ) as get_user:
            user = await self._call(
                DummyRequest(headers={"authorization": f"bearer {token}"})
            )
        get_user.assert_awaited_once_with(5)
        self.assertIs(fake_user, user)

    async def test_missing_jwt_secret_returns_50000(self):
        token, _ = tokens.create_access_token(1)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_SECRET", None)
            with capture_logger(service_logger):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await self._call(
                        DummyRequest(headers={"Authorization": f"Bearer {token}"})
                    )
        self.assertEqual(500, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_INTERNAL, ctx.exception.code)


class ResponseEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    def test_ok_envelope_with_request_id(self):
        request = DummyRequest(request_id="req_abc123")
        body = v1_common.ok({"x": 1}, request=request)
        self.assertEqual(200, body["code"])
        self.assertEqual("OK", body["message"])
        self.assertEqual({"x": 1}, body["data"])
        self.assertEqual("req_abc123", body["request_id"])

    def test_ok_envelope_without_request(self):
        body = v1_common.ok()
        self.assertEqual({"code": 200, "message": "OK", "data": None}, body)

    async def test_api_error_handler_returns_v1_body(self):
        request = DummyRequest(request_id="req_err456")
        response = await v1_common.api_error_handler(
            request, v1_common.APIError(429, v1_common.CODE_RATE_LIMITED, "太频繁")
        )
        self.assertEqual(429, response.status_code)
        body = _body(response)
        self.assertEqual(v1_common.CODE_RATE_LIMITED, body["code"])
        self.assertEqual("太频繁", body["message"])
        self.assertIsNone(body["data"])
        self.assertEqual("req_err456", body["request_id"])


class LoginEndpointTests(AuthTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_login_success_returns_token_and_user(self):
        request = DummyRequest(request_id="req_login")
        payload = SimpleNamespace(username="alice", password="Pa55w0rd!")
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": True, "user": {"id": 1, "username": "alice"}}),
        ) as service_login:
            response = await v1_auth.login(request, payload)

        service_login.assert_awaited_once_with("alice", "Pa55w0rd!")
        self.assertEqual(200, response["code"])
        self.assertEqual("req_login", response["request_id"])
        data = response["data"]
        self.assertEqual("Bearer", data["token_type"])
        self.assertEqual(7 * 24 * 3600, data["expires_in"])
        self.assertEqual({"id": "1", "username": "alice"}, data["user"])
        self.assertEqual("1", tokens.decode_access_token(data["access_token"]))

    async def test_login_wrong_password_returns_401(self):
        payload = SimpleNamespace(username="alice", password="wrong")
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": False, "message": "用户名或密码错误"}),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_auth.login(DummyRequest(), payload)

        self.assertEqual(401, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_UNAUTHORIZED, ctx.exception.code)
        self.assertEqual("用户名或密码错误", ctx.exception.message)

    async def test_login_missing_fields_returns_400(self):
        for payload in (
            SimpleNamespace(username="", password="x"),
            SimpleNamespace(username="alice", password=""),
            SimpleNamespace(username="   ", password="x"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await v1_auth.login(DummyRequest(), payload)
                self.assertEqual(400, ctx.exception.status_code)
                self.assertEqual(v1_common.CODE_BAD_REQUEST, ctx.exception.code)

    async def test_login_without_jwt_secret_returns_50000(self):
        payload = SimpleNamespace(username="alice", password="pw")
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": True, "user": {"id": 1, "username": "alice"}}),
        ), mock.patch.dict(os.environ, {}, clear=False), capture_logger(service_logger):
            os.environ.pop("JWT_SECRET", None)
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_auth.login(DummyRequest(), payload)

        self.assertEqual(500, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_INTERNAL, ctx.exception.code)


class MeEndpointTests(AuthTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_me_returns_current_user(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        user = SimpleNamespace(id=3, username="bob", created_at=created)
        request = DummyRequest(request_id="req_me")
        response = await v1_auth.get_me(request, user)

        self.assertEqual(200, response["code"])
        self.assertEqual(
            {
                "id": "3",
                "username": "bob",
                "created_at": int(created.timestamp() * 1000),
            },
            response["data"],
        )
        self.assertEqual("req_me", response["request_id"])
        self.assertNotIn("password", json.dumps(response))

    async def test_me_without_created_at(self):
        user = SimpleNamespace(id=3, username="bob", created_at=None)
        response = await v1_auth.get_me(DummyRequest(), user)
        self.assertIsNone(response["data"]["created_at"])


class ApiInitRegistrationTests(unittest.TestCase):
    def test_create_app_registers_v1_router_and_error_handler(self):
        source = (REPO_ROOT / "api" / "init.py").read_text(encoding="utf-8")
        self.assertIn("include_router(v1_router)", source)
        self.assertIn("add_exception_handler(APIError, api_error_handler)", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
