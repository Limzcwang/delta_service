"""FastAPI WebSocket 端点 ``/ws``（B2b：JWT 认证 + 房间频道）。

复用 ``websocket/websocket_manager.py`` 中与传输无关的会话/认证/频道协议引擎；
这里只负责把 FastAPI WebSocket 适配成引擎需要的 ``send``/``close`` 接口。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from log.logger import log_exception
from websocket.websocket_manager import ClientSession, websocket_manager

router = APIRouter()


class FastAPIWebSocketAdapter:
    """把 FastAPI/Starlette WebSocket 适配为协议引擎需要的接口。"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def send(self, message: str) -> None:
        await self.websocket.send_text(message)

    async def close(self, code: int = 1000, reason: str = None) -> None:
        try:
            await self.websocket.close(code=code, reason=reason)
        except TypeError:  # 兼容不支持 reason 参数的实现
            await self.websocket.close(code=code)
        except Exception:  # 连接可能已关闭
            pass


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """单端口（8000）WebSocket：/ws。

    客户端连接后首条消息必须是 ``{"type":"auth","token":"<JWT>"}``。
    """
    await websocket.accept()

    adapter = FastAPIWebSocketAdapter(websocket)
    session = ClientSession(websocket=adapter)
    heartbeat_task = asyncio.create_task(websocket_manager.send_heartbeat(adapter))

    try:
        while True:
            message = await websocket.receive_text()
            await websocket_manager.process_message(session, message)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - 防御性
        log_exception(exc, "FastAPI WebSocket 处理异常")
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await websocket_manager._cleanup_session(
            session, reason="disconnected"
        )
