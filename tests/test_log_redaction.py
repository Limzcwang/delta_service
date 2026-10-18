"""A）SQL 参数脱敏：纯函数与日志写入路径的单元测试。"""

import unittest

from log.logger import (
    SQL_REDACTED,
    GlobalLogger,
    log_sql_execution,
    logger as service_logger,
    redact_sql_params,
)
from tests.support import capture_logger


class RedactSqlParamsTests(unittest.TestCase):
    """redact_sql_params(sql, params) 的契约测试。"""

    def test_none_params_always_returns_none(self):
        self.assertIsNone(redact_sql_params("SELECT * FROM users WHERE password=%s", None))
        self.assertIsNone(redact_sql_params("SELECT 1", None))
        # 防御性：sql 非字符串时也不应抛错
        self.assertIsNone(redact_sql_params(None, None))

    def test_non_sensitive_sql_returns_params_unchanged(self):
        params = ["alice", 1]
        result = redact_sql_params(
            "SELECT * FROM users WHERE username=%s AND id=%s", params
        )
        self.assertIs(params, result)
        self.assertEqual(["alice", 1], result)

    def test_non_sensitive_dict_params_unchanged(self):
        params = {"username": "alice", "limit": 10}
        result = redact_sql_params(
            "SELECT * FROM users WHERE username=:username LIMIT :limit", params
        )
        self.assertIs(params, result)

    def test_empty_params_on_non_sensitive_sql_unchanged(self):
        params = []
        result = redact_sql_params("SELECT 1", params)
        self.assertIs(params, result)

    def test_sensitive_markers_are_case_insensitive(self):
        sensitive_sqls = [
            "SELECT * FROM users WHERE password = %s",
            "SELECT * FROM users WHERE PASSWORD = %s",
            "UPDATE users SET Password = %s WHERE id = %s",
            "INSERT INTO users (username, passwd) VALUES (%s, %s)",
            "SELECT * FROM users WHERE reset_token = %s",
            "UPDATE users SET client_secret = %s WHERE id = %s",
            "INSERT INTO credentials (user_id, credential) VALUES (%s, %s)",
            "SELECT * FROM users WHERE pwd = %s",
            "SELECT * FROM users WHERE api_key = %s",
            "SELECT * FROM users WHERE APIKEY = %s",
        ]
        params = ["alice", "Pa55w0rd!"]
        for sql in sensitive_sqls:
            with self.subTest(sql=sql):
                self.assertEqual(SQL_REDACTED, redact_sql_params(sql, params))

    def test_sensitive_sql_redacts_regardless_of_param_type(self):
        sql = "UPDATE users SET password = %s WHERE id = %s"
        for params in (["a", "b"], ("a", "b"), {"password": "x"}, "raw-string", [], ""):
            with self.subTest(params=repr(params)):
                self.assertEqual(SQL_REDACTED, redact_sql_params(sql, params))

    def test_lookalike_tables_and_columns_are_not_redacted(self):
        params = ["alice", 1]
        for sql in (
            "SELECT * FROM user_sessions WHERE session_id = %s",
            "SELECT * FROM settings WHERE setting_name = %s",
            "SELECT * FROM orders WHERE status = %s",
        ):
            with self.subTest(sql=sql):
                self.assertIs(params, redact_sql_params(sql, params))

    def test_redaction_is_idempotent(self):
        sql = "SELECT * FROM users WHERE password = %s"
        once = redact_sql_params(sql, ["Pa55w0rd!"])
        twice = redact_sql_params(sql, once)
        self.assertEqual(SQL_REDACTED, once)
        self.assertEqual(once, twice)

    def test_redacted_marker_contract(self):
        self.assertEqual("***REDACTED***", SQL_REDACTED)


class SqlLogExecutionRedactionTests(unittest.TestCase):
    """log_sql_execution() 写日志时必须打码且保持原有格式。"""

    def test_login_sql_params_are_redacted_in_log(self):
        with capture_logger(service_logger) as capture:
            log_sql_execution(
                "SELECT * FROM users WHERE username=%s AND password=%s",
                ["alice", "Pa55w0rd!"],
                duration=0.01,
            )

        self.assertEqual(1, len(capture.records))
        message = capture.messages()[0]
        self.assertIn(SQL_REDACTED, message)
        # 用户名和口令都不能出现在日志里（整体打码）
        self.assertNotIn("Pa55w0rd!", message)
        self.assertNotIn("alice", message)
        self.assertTrue(message.startswith("SQL执行 - 成功 - "))

    def test_non_sensitive_sql_keeps_params_and_log_format(self):
        with capture_logger(service_logger) as capture:
            log_sql_execution(
                "SELECT * FROM users WHERE username=%s",
                ["alice"],
                duration=0.01,
                success=False,
                error="timeout",
            )

        message = capture.messages()[0]
        self.assertTrue(message.startswith("SQL执行 - 失败 - "))
        self.assertIn("SELECT * FROM users WHERE username=%s", message)
        self.assertIn("参数:['alice']", message)
        self.assertIn("耗时:0.010s", message)
        self.assertIn("错误:timeout", message)
        self.assertNotIn(SQL_REDACTED, message)

    def test_none_params_keeps_log_format_without_param_segment(self):
        with capture_logger(service_logger) as capture:
            log_sql_execution("SELECT 1", None)

        message = capture.messages()[0]
        self.assertNotIn("参数:", message)
        self.assertIn("SQL执行 - 成功 - SELECT 1", message)

    def test_global_logger_sql_result_redacts_sensitive_params(self):
        # sql_logger 中间件通过 GlobalLogger.sql_query/sql_result 落日志，
        # 即使调用方忘记预处理，最终写日志前也必须打码（纵深防御）。
        context = GlobalLogger.sql_query(
            "UPDATE users SET password=%s WHERE id=%s", ["Pa55w0rd!", 7]
        )
        with capture_logger(service_logger) as capture:
            GlobalLogger.sql_result(context, True)

        message = capture.messages()[0]
        self.assertIn(SQL_REDACTED, message)
        self.assertNotIn("Pa55w0rd!", message)
        self.assertNotIn("7", message)

    def test_update_and_insert_password_statements_are_redacted(self):
        for sql in (
            "UPDATE users SET password = %s WHERE id = %s",
            "INSERT INTO users (username, password) VALUES (%s, %s)",
        ):
            with self.subTest(sql=sql):
                with capture_logger(service_logger) as capture:
                    log_sql_execution(sql, ["alice", "Pa55w0rd!"])
                message = capture.messages()[0]
                self.assertIn(SQL_REDACTED, message)
                self.assertNotIn("Pa55w0rd!", message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
