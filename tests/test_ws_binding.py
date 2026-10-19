"""修复 2：WebSocket 监听地址可由 WS_HOST/WS_PORT 配置，默认 0.0.0.0:8765。"""

import importlib
import os
import sys
import types
import unittest
from unittest import mock

from tests.support import REPO_ROOT, capture_logger, install_tortoise_stub

install_tortoise_stub()


def _install_websockets_stub():
    """websockets 库未安装时提供最小替身，供 manager 模块导入。"""
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

    sys.modules["websockets"] = ws
    sys.modules["websockets.server"] = server
    sys.modules["websockets.exceptions"] = exceptions


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


class WsBindAddressTests(unittest.TestCase):
    def test_default_is_all_interfaces_and_8765(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WS_HOST", None)
            os.environ.pop("WS_PORT", None)
            self.assertEqual(
                ("0.0.0.0", 8765), websocket_manager_module.get_ws_bind_address()
            )

    def test_environment_variables_override_defaults(self):
        with mock.patch.dict(
            os.environ, {"WS_HOST": "127.0.0.1", "WS_PORT": "9001"}, clear=False
        ):
            self.assertEqual(
                ("127.0.0.1", 9001), websocket_manager_module.get_ws_bind_address()
            )

    def test_explicit_arguments_override_environment(self):
        with mock.patch.dict(
            os.environ, {"WS_HOST": "127.0.0.1", "WS_PORT": "9001"}, clear=False
        ):
            self.assertEqual(
                ("10.0.0.5", 9100),
                websocket_manager_module.get_ws_bind_address("10.0.0.5", 9100),
            )

    def test_blank_host_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"WS_HOST": "   "}, clear=False):
            host, _port = websocket_manager_module.get_ws_bind_address()
        self.assertEqual("0.0.0.0", host)

    def test_invalid_ports_raise_value_error(self):
        for raw_port in ("abc", "0", "65536", "-1", "8765.5"):
            with self.subTest(raw_port=raw_port):
                with mock.patch.dict(
                    os.environ, {"WS_HOST": "0.0.0.0", "WS_PORT": raw_port}, clear=False
                ):
                    with self.assertRaises(ValueError):
                        websocket_manager_module.get_ws_bind_address()


class WsServeWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_websocket_server_uses_resolved_host_and_port(self):
        entered = []
        serve_calls = []

        class _FakeServe:
            async def __aenter__(self):
                entered.append(True)
                raise RuntimeError("stop-after-serve")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def fake_serve(handler, host, port, **kwargs):
            serve_calls.append((handler, host, port, kwargs))
            return _FakeServe()

        manager = websocket_manager_module.WebSocketManager()
        with mock.patch.object(
            websocket_manager_module.websockets, "serve", fake_serve
        ), mock.patch.dict(
            os.environ, {"WS_HOST": "127.0.0.1", "WS_PORT": "9001"}, clear=False
        ), capture_logger(service_logger):
            with self.assertRaises(RuntimeError):
                await manager.run_websocket_server()

        self.assertEqual([True], entered)
        self.assertEqual(1, len(serve_calls))
        _handler, host, port, kwargs = serve_calls[0]
        self.assertEqual("127.0.0.1", host)
        self.assertEqual(9001, port)
        self.assertEqual(20, kwargs["ping_interval"])
        self.assertEqual(10, kwargs["ping_timeout"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
