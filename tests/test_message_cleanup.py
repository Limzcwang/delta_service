"""C 修复：30 天消息清理周期任务测试（可注入 sleep/clock，无真实等待）。"""

import asyncio
import os
import unittest
from unittest import mock

from tests.support import capture_logger, install_tortoise_stub

install_tortoise_stub()

from log.logger import logger as service_logger  # noqa: E402
from service import message_cleanup as cleanup_module  # noqa: E402
from service.message_cleanup import MessageCleanupTask, resolve_interval_hours  # noqa: E402


class IntervalConfigTests(unittest.TestCase):
    def test_default_is_24_hours(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(cleanup_module.ENV_INTERVAL_HOURS, None)
            self.assertEqual(24.0, resolve_interval_hours())

    def test_env_override_and_explicit_value(self):
        with mock.patch.dict(
            os.environ, {cleanup_module.ENV_INTERVAL_HOURS: "6"}, clear=False
        ):
            self.assertEqual(6.0, resolve_interval_hours())
        self.assertEqual(0.5, resolve_interval_hours(0.5))

    def test_invalid_values_fall_back_to_default(self):
        for value in ("abc", "0", "-1"):
            with self.subTest(value=value):
                with capture_logger(service_logger):
                    self.assertEqual(24.0, resolve_interval_hours(value))


class CleanupTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)

    async def test_run_once_returns_deleted_count(self):
        cleanup = mock.AsyncMock(return_value=7)
        task = MessageCleanupTask(interval_hours=1, cleanup=cleanup)
        self.assertEqual(7, await task.run_once())
        cleanup.assert_awaited_once()

    async def test_run_once_swallows_exception(self):
        cleanup = mock.AsyncMock(side_effect=RuntimeError("db down"))
        task = MessageCleanupTask(interval_hours=1, cleanup=cleanup)
        self.assertEqual(0, await task.run_once())

    async def test_loop_runs_immediately_then_sleeps_interval(self):
        cleanup = mock.AsyncMock(return_value=3)
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        task = MessageCleanupTask(
            interval_hours=2, cleanup=cleanup, sleep=fake_sleep
        )
        background = task.start()
        with self.assertRaises(asyncio.CancelledError):
            await background

        cleanup.assert_awaited_once()
        self.assertEqual([2 * 3600], sleep_calls)

    async def test_loop_continues_after_cleanup_exception(self):
        cleanup = mock.AsyncMock(side_effect=[RuntimeError("once"), 5])
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        task = MessageCleanupTask(
            interval_hours=1, cleanup=cleanup, sleep=fake_sleep
        )
        background = task.start()
        with self.assertRaises(asyncio.CancelledError):
            await background

        self.assertEqual(2, cleanup.await_count)
        self.assertEqual(2, len(sleep_calls))

    async def test_stop_cancels_sleeping_task(self):
        cleanup = mock.AsyncMock(return_value=1)
        started_sleeping = asyncio.Event()

        async def fake_sleep(seconds):
            started_sleeping.set()
            await asyncio.Event().wait()

        task = MessageCleanupTask(
            interval_hours=1, cleanup=cleanup, sleep=fake_sleep
        )
        background = task.start()
        await asyncio.wait_for(started_sleeping.wait(), timeout=1)
        await task.stop()

        self.assertTrue(background.cancelled())
        cleanup.assert_awaited_once()
        await task.stop()  # 幂等


class AppWiringTests(unittest.TestCase):
    def test_app_startup_starts_and_shutdown_stops_cleanup(self):
        from pathlib import Path

        source = Path("api/init.py").read_text(encoding="utf-8")
        self.assertIn("MessageCleanupTask", source)
        self.assertIn("message_cleanup.start()", source)
        self.assertIn("await message_cleanup.stop()", source)
        # 不得挂在 legacy 8765 开关后面
        self.assertNotIn("LEGACY_WS_ENABLED", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
