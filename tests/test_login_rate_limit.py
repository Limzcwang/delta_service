"""B1：登录限频测试（内存滑动窗口 + 端点集成，注入时钟不 sleep）。"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    install_fastapi_stub,
    install_jwt_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()
install_jwt_stub()

from api import v1_auth, v1_common  # noqa: E402
from security.login_limit import LoginRateLimiter, build_login_rate_limiter  # noqa: E402

SECRET = "unit-test-jwt-secret-0123456789abcdef"


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class DummyRequest:
    def __init__(self, ip="203.0.113.10", request_id=None):
        self.client = SimpleNamespace(host=ip)
        self.state = SimpleNamespace()
        self.headers = {}
        if request_id is not None:
            self.state.request_id = request_id


class LoginRateLimiterUnitTests(unittest.TestCase):
    def test_five_attempts_allowed_then_blocked(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=5, window_seconds=60, clock=clock)

        for attempt in range(5):
            with self.subTest(attempt=attempt):
                self.assertTrue(limiter.check("1.1.1.1"))
        self.assertFalse(limiter.check("1.1.1.1"))

    def test_window_expiry_restores_capacity(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=5, window_seconds=60, clock=clock)
        for _ in range(5):
            self.assertTrue(limiter.check("1.1.1.1"))
        self.assertFalse(limiter.check("1.1.1.1"))

        clock.advance(61)
        self.assertTrue(limiter.check("1.1.1.1"))

    def test_limits_are_per_key(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, clock=clock)
        self.assertTrue(limiter.check("1.1.1.1"))
        self.assertTrue(limiter.check("1.1.1.1"))
        self.assertFalse(limiter.check("1.1.1.1"))
        # 另一个 IP 不受影响
        self.assertTrue(limiter.check("2.2.2.2"))

    def test_retry_after_and_reset(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, clock=clock)
        self.assertEqual(0, limiter.retry_after("1.1.1.1"))
        limiter.check("1.1.1.1")
        limiter.check("1.1.1.1")
        self.assertGreater(limiter.retry_after("1.1.1.1"), 0)

        limiter.reset("1.1.1.1")
        self.assertEqual(0, limiter.retry_after("1.1.1.1"))
        self.assertTrue(limiter.check("1.1.1.1"))

    def test_invalid_construction(self):
        for max_attempts, window in ((0, 60), (-1, 60), (5, 0), (5, -1)):
            with self.subTest(max_attempts=max_attempts, window=window):
                with self.assertRaises(ValueError):
                    LoginRateLimiter(max_attempts=max_attempts, window_seconds=window)

    def test_builder_reads_environment(self):
        with mock.patch.dict(
            os.environ,
            {
                "LOGIN_RATE_LIMIT_PER_MIN": "2",
                "LOGIN_RATE_LIMIT_WINDOW_SECONDS": "30",
            },
            clear=False,
        ):
            limiter = build_login_rate_limiter()
        self.assertEqual(2, limiter.max_attempts)
        self.assertEqual(30, limiter.window_seconds)

    def test_builder_rejects_invalid_environment(self):
        for raw in ("abc", "0", "-1"):
            with self.subTest(raw=raw):
                with mock.patch.dict(
                    os.environ, {"LOGIN_RATE_LIMIT_PER_MIN": raw}, clear=False
                ):
                    with self.assertRaises(ValueError):
                        build_login_rate_limiter()


class LoginEndpointRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": SECRET, "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.clock = FakeClock()
        self.original_limiter = v1_auth.login_rate_limiter
        v1_auth.login_rate_limiter = LoginRateLimiter(
            max_attempts=5, window_seconds=60, clock=self.clock
        )
        self.addCleanup(setattr, v1_auth, "login_rate_limiter", self.original_limiter)

    async def _login(self, username="alice", password="pw", ip="203.0.113.10"):
        payload = SimpleNamespace(username=username, password=password)
        return await v1_auth.login(DummyRequest(ip=ip), payload)

    async def test_sixth_attempt_from_same_ip_returns_429(self):
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": True, "user": {"id": 1, "username": "alice"}}),
        ):
            for _ in range(5):
                response = await self._login()
                self.assertEqual(200, response["code"])

            with self.assertRaises(v1_common.APIError) as ctx:
                await self._login()

        error = ctx.exception
        self.assertEqual(429, error.status_code)
        self.assertEqual(v1_common.CODE_RATE_LIMITED, error.code)
        self.assertIn("Retry-After", error.headers)
        self.assertGreaterEqual(int(error.headers["Retry-After"]), 1)

    async def test_other_ip_is_not_blocked(self):
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": True, "user": {"id": 1, "username": "alice"}}),
        ):
            for _ in range(5):
                await self._login(ip="203.0.113.10")
            with self.assertRaises(v1_common.APIError):
                await self._login(ip="203.0.113.10")

            # 另一个 IP 仍可登录
            response = await self._login(ip="198.51.100.7")
        self.assertEqual(200, response["code"])

    async def test_failed_attempts_also_count(self):
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": False, "message": "用户名或密码错误"}),
        ):
            for _ in range(5):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await self._login(password="wrong")
                self.assertEqual(v1_common.CODE_UNAUTHORIZED, ctx.exception.code)

            with self.assertRaises(v1_common.APIError) as ctx:
                await self._login(password="wrong")
        self.assertEqual(429, ctx.exception.status_code)

    async def test_capacity_restored_after_window(self):
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(return_value={"success": True, "user": {"id": 1, "username": "alice"}}),
        ):
            for _ in range(5):
                await self._login()
            with self.assertRaises(v1_common.APIError):
                await self._login()

            self.clock.advance(61)
            response = await self._login()
        self.assertEqual(200, response["code"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
