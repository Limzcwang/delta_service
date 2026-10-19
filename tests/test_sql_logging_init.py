"""修复 4：SQL 日志初始化幂等（重复调用只注册一次中间件）。"""

import asyncio
import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.support import install_tortoise_stub

install_tortoise_stub()

from log import sql_logger  # noqa: E402

tortoise_module = sys.modules["tortoise"]


class _FakeConnection:
    def __init__(self):
        self._query_middleware = []


class _FakeTortoise:
    def __init__(self):
        self.connection = _FakeConnection()

    def get_connection(self, name):
        return self.connection


class SqlLoggingInitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake_tortoise = _FakeTortoise()
        self.original_tortoise = getattr(tortoise_module, "Tortoise", None)
        tortoise_module.Tortoise = self.fake_tortoise
        # 每个用例从“未初始化”状态开始
        sql_logger._sql_logging_initialized = False
        self.addCleanup(self._restore)

    def _restore(self):
        sql_logger._sql_logging_initialized = False
        if self.original_tortoise is None:
            if hasattr(tortoise_module, "Tortoise"):
                delattr(tortoise_module, "Tortoise")
        else:
            tortoise_module.Tortoise = self.original_tortoise

    async def test_repeated_init_registers_middleware_only_once(self):
        with redirect_stdout(io.StringIO()):
            await sql_logger.init_sql_logging()
            self.assertTrue(sql_logger.is_sql_logging_initialized())
            await sql_logger.init_sql_logging()
            await sql_logger.init_sql_logging()

        middleware = self.fake_tortoise.connection._query_middleware
        self.assertEqual(1, len(middleware))
        self.assertIs(sql_logger.sql_logger_middleware, middleware[0])

    async def test_concurrent_init_registers_middleware_only_once(self):
        with redirect_stdout(io.StringIO()):
            await asyncio.gather(
                sql_logger.init_sql_logging(),
                sql_logger.init_sql_logging(),
                sql_logger.init_sql_logging(),
            )

        self.assertEqual(
            1, len(self.fake_tortoise.connection._query_middleware)
        )

    async def test_init_is_noop_when_already_initialized(self):
        sql_logger._sql_logging_initialized = True
        with mock.patch.object(
            tortoise_module.Tortoise,
            "get_connection",
            side_effect=AssertionError("已初始化时不应再次访问数据库连接"),
        ), redirect_stdout(io.StringIO()):
            await sql_logger.init_sql_logging()
        self.assertTrue(sql_logger.is_sql_logging_initialized())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
