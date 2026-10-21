"""启动自动迁移 + 关键列自检 + 部署接线测试。"""

import os
import unittest
from unittest import mock

from tests.support import capture_logger, install_fastapi_stub, install_tortoise_stub, REPO_ROOT

install_tortoise_stub()
install_fastapi_stub()

from service import startup_migration as sm  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402


class SchemaCheckTests(unittest.TestCase):
    def test_missing_columns_detected(self):
        class Cursor:
            def close(self):
                pass

        class Connection:
            def cursor(self):
                return Cursor()

        with mock.patch.object(sm, "_table_exists", side_effect=[True, False, True]), mock.patch.object(
            sm, "_column_exists", side_effect=[False, False]
        ):
            missing = sm.check_required_columns(Connection())
        self.assertEqual(
            ["users.nickname", "rooms.*", "room_skill_states.squad_number"],
            missing,
        )


class StartupMigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.capture = capture_logger(service_logger)
        self.handler = self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    async def test_success_logs_stats_and_runs_migration(self):
        connection = mock.Mock()
        with mock.patch.object(
            sm, "build_connection", return_value=connection
        ), mock.patch.object(
            sm, "migrate", return_value={"squad_number_added": 1}
        ) as migrate, mock.patch.object(
            sm, "check_required_columns", return_value=[]
        ), mock.patch.dict(os.environ, {"AUTO_MIGRATE_ON_STARTUP": "true"}):
            await sm.run_startup_migrations()
        migrate.assert_called_once_with(connection)
        connection.close.assert_called_once()
        self.assertIn("启动数据库迁移完成", "\n".join(self.handler.messages()))
        self.assertIn("squad_number_added", "\n".join(self.handler.messages()))

    async def test_disabled_skips_migration_but_checks(self):
        connection = mock.Mock()
        with mock.patch.object(
            sm, "build_connection", return_value=connection
        ), mock.patch.object(sm, "migrate") as migrate, mock.patch.object(
            sm, "check_required_columns", return_value=[]
        ), mock.patch.dict(os.environ, {"AUTO_MIGRATE_ON_STARTUP": "false"}):
            await sm.run_startup_migrations()
        migrate.assert_not_called()
        self.assertIn("跳过启动迁移", "\n".join(self.handler.messages()))

    async def test_failure_does_not_raise(self):
        with mock.patch.object(
            sm, "build_connection", side_effect=RuntimeError("db down")
        ):
            await sm.run_startup_migrations()
        self.assertIn("启动数据库迁移失败", "\n".join(self.handler.messages()))

    async def test_missing_columns_logs_error(self):
        connection = mock.Mock()
        with mock.patch.object(
            sm, "build_connection", return_value=connection
        ), mock.patch.object(sm, "migrate", return_value={}), mock.patch.object(
            sm, "check_required_columns",
            return_value=["room_skill_states.squad_number"],
        ), mock.patch.dict(os.environ, {"AUTO_MIGRATE_ON_STARTUP": "true"}):
            await sm.run_startup_migrations()
        self.assertIn("关键列缺失", "\n".join(self.handler.messages()))


class DeploymentWiringTests(unittest.TestCase):
    def test_main_calls_startup_migration(self):
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("run_startup_migrations()", source)

    def test_ci_runs_migration_and_emits_warning(self):
        source = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("docker exec delta-service-container python scripts/migrate_f.py", source)
        self.assertIn("::warning title=数据库迁移失败", source)

    def test_deployment_docs_describe_auto_migration(self):
        source = (REPO_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn("AUTO_MIGRATE_ON_STARTUP", source)
        self.assertIn("新增字段", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
