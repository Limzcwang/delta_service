"""A）SQL 日志中间件集成测试：写日志前必须打码，但真实查询参数保持原样。"""

import importlib
import sys
import unittest

from tests.support import capture_logger, install_tortoise_stub

# 先把 tortoise 替身装好，再导入 log.sql_logger，避免离线环境缺依赖。
install_tortoise_stub()
sys.modules.pop("log.sql_logger", None)
sql_logger = importlib.import_module("log.sql_logger")

from log.logger import SQL_REDACTED, logger as service_logger  # noqa: E402


class FakeDBClient:
    """记录实际下发给数据库的 (query, params)，用于确认脱敏只影响日志。"""

    def __init__(self):
        self.calls = []

    async def execute_query(self, query, params):
        self.calls.append(("execute_query", query, params))
        return []

    async def execute_inset(self, query, params):
        self.calls.append(("execute_inset", query, params))
        return 1

    async def execute_many(self, query, params):
        self.calls.append(("execute_many", query, params))
        return None


class SqlLoggerMiddlewareRedactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeDBClient()
        self.middleware = sql_logger.SQLLoggerMiddleware()

    async def test_execute_query_redacts_sensitive_params_in_log(self):
        query = "SELECT * FROM users WHERE username=%s AND password=%s"
        params = ["alice", "Pa55w0rd!"]
        with capture_logger(service_logger) as capture:
            await self.middleware.execute_query(query, params, self.client)

        # 数据库仍收到原始参数（脱敏只作用于日志，不能影响业务）
        self.assertEqual([("execute_query", query, params)], self.client.calls)

        message = capture.messages()[0]
        self.assertIn(SQL_REDACTED, message)
        self.assertNotIn("Pa55w0rd!", message)
        self.assertNotIn("alice", message)

    async def test_execute_inset_redacts_sensitive_params_in_log(self):
        query = "INSERT INTO users (username, password) VALUES (%s, %s)"
        params = ["alice", "Pa55w0rd!"]
        with capture_logger(service_logger) as capture:
            await self.middleware.execute_inset(query, params, self.client)

        self.assertEqual([("execute_inset", query, params)], self.client.calls)
        message = capture.messages()[0]
        self.assertIn(SQL_REDACTED, message)
        self.assertNotIn("Pa55w0rd!", message)

    async def test_execute_many_redacts_sensitive_params_in_log(self):
        query = "UPDATE users SET reset_token = %s WHERE id = %s"
        params = [["token-a", 1], ["token-b", 2]]
        with capture_logger(service_logger) as capture:
            await self.middleware.execute_many(query, params, self.client)

        self.assertEqual([("execute_many", query, params)], self.client.calls)
        message = capture.messages()[0]
        self.assertIn(SQL_REDACTED, message)
        self.assertNotIn("token-a", message)
        self.assertNotIn("token-b", message)

    async def test_non_sensitive_query_keeps_params_in_log(self):
        query = "SELECT * FROM users WHERE username=%s"
        params = ["alice"]
        with capture_logger(service_logger) as capture:
            await self.middleware.execute_query(query, params, self.client)

        message = capture.messages()[0]
        self.assertIn("参数:['alice']", message)
        self.assertNotIn(SQL_REDACTED, message)

    async def test_disabled_middleware_does_not_log(self):
        # enabled=False 时直接透传，不产生 SQL 日志（保持原有行为）
        self.middleware.enabled = False
        with capture_logger(service_logger) as capture:
            await self.middleware.execute_query(
                "SELECT * FROM users WHERE password=%s", ["Pa55w0rd!"], self.client
            )

        self.assertEqual([], capture.records)
        self.assertEqual(1, len(self.client.calls))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
