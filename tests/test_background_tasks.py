"""修复 3：后台任务只保留一个启动点，WS server 重复启动不叠加。"""

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO_ROOT, capture_logger, install_tortoise_stub

install_tortoise_stub()


def _install_websockets_stub():
    if getattr(sys.modules.get("websockets.exceptions", None), "_dsh_stub", False):
        return
    ws = types.ModuleType("websockets")
    server = types.ModuleType("websockets.server")
    exceptions = types.ModuleType("websockets.exceptions")

    class WebSocketServerProtocol:
        pass

    class ConnectionClosed(Exception):
        pass

    server.WebSocketServerProtocol = WebSocketServerProtocol
    exceptions.ConnectionClosed = ConnectionClosed
    exceptions._dsh_stub = True
    ws.server = server
    ws.exceptions = exceptions
    ws.serve = lambda *args, **kwargs: None
    ws.broadcast = lambda *args, **kwargs: None
    sys.modules.update(
        {
            "websockets": ws,
            "websockets.server": server,
            "websockets.exceptions": exceptions,
        }
    )


def _load_websocket_manager_module():
    _install_websockets_stub()
    package = sys.modules.get("websocket")
    if package is None or not getattr(package, "__path__", None):
        package = types.ModuleType("websocket")
        package.__path__ = [str(REPO_ROOT / "websocket")]
        sys.modules["websocket"] = package
    return importlib.import_module("websocket.websocket_manager")


websocket_manager_module = _load_websocket_manager_module()
from log.logger import logger as service_logger  # noqa: E402


class _FakeServe:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BackgroundTaskStartupTests(unittest.IsolatedAsyncioTestCase):
    async def _start_server(self, manager, serve_calls):
        def fake_serve(handler, host, port, **kwargs):
            serve_calls.append((handler, host, port, kwargs))
            return _FakeServe()

        patcher = mock.patch.object(
            websocket_manager_module.websockets, "serve", fake_serve
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        task = asyncio.create_task(manager.run_websocket_server())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if serve_calls:
                break
        return task

    async def _cancel(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    async def test_server_does_not_start_background_tasks(self):
        manager = websocket_manager_module.WebSocketManager()
        manager.data_pusher = mock.AsyncMock()
        manager.inactive_user_checker = mock.AsyncMock()
        serve_calls = []

        with capture_logger(service_logger):
            task = await self._start_server(manager, serve_calls)
            try:
                self.assertEqual(1, len(serve_calls))
                self.assertTrue(manager._server_running)
                manager.data_pusher.assert_not_called()
                manager.inactive_user_checker.assert_not_called()
            finally:
                await self._cancel(task)
        self.assertFalse(manager._server_running)

    async def test_repeated_run_is_ignored_while_running(self):
        manager = websocket_manager_module.WebSocketManager()
        serve_calls = []

        with capture_logger(service_logger):
            task = await self._start_server(manager, serve_calls)
            try:
                self.assertEqual(1, len(serve_calls))
                # 第二次调用应被 guard 直接忽略，不会启动第二个 server
                await manager.run_websocket_server()
                self.assertEqual(1, len(serve_calls))
            finally:
                await self._cancel(task)

    async def test_server_can_restart_after_being_cancelled(self):
        manager = websocket_manager_module.WebSocketManager()
        serve_calls = []

        with capture_logger(service_logger):
            first = await self._start_server(manager, serve_calls)
            await self._cancel(first)
            self.assertFalse(manager._server_running)

            second = await self._start_server(manager, serve_calls)
            try:
                self.assertEqual(2, len(serve_calls))
            finally:
                await self._cancel(second)


class BackgroundStartupSourceTests(unittest.TestCase):
    """结构性回归：后台任务只在 main.py 启动，manager 内不得再 create_task。"""

    def test_main_is_the_only_background_task_startup_point(self):
        main_source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        manager_source = Path(websocket_manager_module.__file__).read_text(encoding="utf-8")

        self.assertIn("websocket_manager.data_pusher()", main_source)
        self.assertIn("websocket_manager.inactive_user_checker()", main_source)
        self.assertNotIn("create_task(self.data_pusher()", manager_source)
        self.assertNotIn("create_task(self.inactive_user_checker()", manager_source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
