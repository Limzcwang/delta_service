"""WebSocket 离线测试辅助：websockets stub + manager 加载 + 假连接。"""

import importlib
import json
import sys
import types
from types import SimpleNamespace

from tests.support import REPO_ROOT, install_tortoise_stub


def install_websockets_stub():
    """websockets 库未安装时提供最小替身。"""
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


def load_websocket_manager_module():
    install_tortoise_stub()
    install_websockets_stub()
    package = sys.modules.get("websocket")
    if package is None or not getattr(package, "__path__", None):
        package = types.ModuleType("websocket")
        package.__path__ = [str(REPO_ROOT / "websocket")]
        sys.modules["websocket"] = package
    return importlib.import_module("websocket.websocket_manager")


class FakeWebSocket:
    """记录 send()/close() 的假 WebSocket 连接。"""

    def __init__(self, path="/ws"):
        self.path = path
        self.sent = []
        self.closed = None

    async def send(self, message):
        if isinstance(message, (str, bytes)):
            self.sent.append(json.loads(message))
        else:  # pragma: no cover - 防御性
            self.sent.append(message)

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)

    def messages(self, message_type=None):
        if message_type is None:
            return list(self.sent)
        return [item for item in self.sent if item.get("type") == message_type]

    def last(self):
        return self.sent[-1] if self.sent else None


def make_user(user_id=1, username="alice", nickname=None):
    return SimpleNamespace(
        id=user_id, username=username, nickname=nickname if nickname is not None else username
    )


def make_room(room_id="room-1", code="A1B2C", **overrides):
    from datetime import datetime

    base = datetime(2026, 1, 1, 12, 0, 0)
    values = {
        "id": room_id,
        "code": code,
        "name": "开黑房",
        "visibility": "public",
        "owner_id": 1,
        "password_hash": None,
        "background_id": None,
        "created_at": base,
        "updated_at": base,
        "closed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_session(manager, user, websocket, room=None):
    """构造一个已认证（可选已在房间）的会话并注册到 manager。"""
    session = manager_module.ClientSession(websocket=websocket, user=user)
    session.user_id = str(user.id)
    manager.sessions[session.user_id] = session
    manager.user_websocket_map[user.username] = websocket
    manager.connected_clients.add(websocket)
    if room is not None:
        session.room_id = room.id
        session.room_code = room.code
    return session


manager_module = load_websocket_manager_module()
