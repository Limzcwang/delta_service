"""WebSocket 连接管理器（B2b：JWT 认证 + 房间频道）。

协议要点（与 PROTOCOL.md §6 对齐）：
- 连接后首条消息必须是 ``{"type":"auth","token":"<JWT>"}``；失败回 ``auth_error`` 并关闭；
- 未认证消息一律回 ``NOT_AUTHENTICATED``；
- ``join_room`` 要求已通过 HTTP join 成为房间成员，且房间未关闭；
- 单用户单连接：重复登录时关闭并清理旧连接；
- 聊天/画板/技能消息在 C/D/E 阶段实现，当前返回 ``UNSUPPORTED_MESSAGE_TYPE``。
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Set

import websockets
from websockets.server import WebSocketServerProtocol

from dao.user import UserDao
from log.logger import logger, log_exception, log_websocket_event
from security.tokens import (
    TokenConfigError,
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
)
from service import rooms as rooms_service
from service.rooms import RoomError
from service.user import UserService

# 默认监听所有网卡，容器 -p 8765:8765 才能生效。
# 生产环境必须用云安全组/防火墙或反向代理限制可访问来源。
DEFAULT_WS_HOST = "0.0.0.0"
DEFAULT_WS_PORT = 8765

WS_CLOSE_AUTH_FAILED = 4001
WS_CLOSE_REPLACED = 4000
HEARTBEAT_INTERVAL_SECONDS = 30


def get_ws_bind_address(host: Optional[str] = None, port=None):
    """解析 WebSocket 监听地址。

    优先级：显式参数 > 环境变量（WS_HOST / WS_PORT）> 默认值
    （0.0.0.0:8765）。端口非法时抛 ``ValueError``，避免带着错误配置静默监听。
    """
    resolved_host = host if host is not None else os.getenv("WS_HOST", DEFAULT_WS_HOST)
    resolved_host = (resolved_host or "").strip() or DEFAULT_WS_HOST

    raw_port = port if port is not None else os.getenv("WS_PORT", str(DEFAULT_WS_PORT))
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"WS_PORT 必须是 1-65535 的整数，当前值: {raw_port!r}"
        ) from exc
    if not 1 <= resolved_port <= 65535:
        raise ValueError(f"WS_PORT 必须是 1-65535 的整数，当前值: {resolved_port!r}")
    return resolved_host, resolved_port


@dataclass
class ClientSession:
    """一条 WebSocket 连接的状态。"""

    websocket: Any
    user: Any = None
    user_id: Optional[str] = None
    room_id: Optional[str] = None
    room_code: Optional[str] = None

    @property
    def authenticated(self) -> bool:
        return self.user is not None and self.user_id is not None


class WebSocketManager:
    """WebSocket 连接/认证/房间频道管理器（表现层）。"""

    def __init__(self):
        self.connected_clients: Set[WebSocketServerProtocol] = set()
        # user_id(str) -> ClientSession（单用户单连接）
        self.sessions: Dict[str, ClientSession] = {}
        # 兼容旧代码的 username -> websocket 映射（仅诊断用）
        self.user_websocket_map: Dict[str, WebSocketServerProtocol] = {}
        # 防止重复调用 run_websocket_server 时叠加 server/后台任务
        self._server_running = False

    # ------------------------------------------------------------------ 发送
    @staticmethod
    def _error_payload(code: str, message: str) -> Dict[str, Any]:
        # code 为新协议字段；error_code 为旧字段兼容（两者同值）
        return {
            "type": "error",
            "code": code,
            "error_code": code,
            "message": message,
        }

    @staticmethod
    def _auth_error_payload(code: str, message: str) -> Dict[str, Any]:
        return {
            "type": "auth_error",
            "code": code,
            "error_code": code,
            "message": message,
        }

    @staticmethod
    async def _send(session: ClientSession, payload: Dict[str, Any]) -> None:
        try:
            await session.websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # 连接已关闭等
            logger.debug("WebSocket 发送失败: %s", exc)

    async def _send_error(self, session: ClientSession, code: str, message: str) -> None:
        await self._send(session, self._error_payload(code, message))

    @staticmethod
    async def _close(websocket: Any, code: int, reason: str) -> None:
        try:
            await websocket.close(code=code, reason=reason)
        except Exception:
            pass

    # ------------------------------------------------------------------ 认证
    async def authenticate(self, session: ClientSession, token: Any) -> bool:
        """校验 JWT 并建立 user 会话；失败发 auth_error 并关闭连接。"""
        if session.authenticated:
            await self._send_error(session, "ALREADY_AUTHENTICATED", "已认证，请勿重复认证")
            return False
        if not isinstance(token, str) or not token.strip():
            await self._send_auth_failure(session, "AUTH_INVALID", "认证 token 无效")
            return False

        try:
            user_id = decode_access_token(token.strip())
        except TokenExpiredError:
            await self._send_auth_failure(session, "AUTH_EXPIRED", "登录已过期，请重新登录")
            return False
        except TokenInvalidError:
            await self._send_auth_failure(session, "AUTH_INVALID", "认证 token 无效")
            return False
        except TokenConfigError as exc:
            log_exception(exc, "WS JWT 配置错误")
            await self._send_auth_failure(session, "AUTH_INVALID", "服务端认证配置错误")
            return False

        try:
            user_pk = int(user_id)
        except (TypeError, ValueError):
            await self._send_auth_failure(session, "AUTH_INVALID", "认证 token 无效")
            return False

        user = await UserDao.get_user_by_id(user_pk)  # 已过滤 deleted
        if user is None:
            await self._send_auth_failure(session, "AUTH_INVALID", "认证 token 无效")
            return False

        # 单用户单连接：顶掉旧连接
        await self._replace_existing_session(session, user)

        session.user = user
        session.user_id = str(getattr(user, "id", ""))
        self.sessions[session.user_id] = session
        username = getattr(user, "username", None)
        if username:
            self.user_websocket_map[username] = session.websocket

        await self._send(
            session,
            {
                "type": "auth_ok",
                "user": {
                    "id": session.user_id,
                    "username": username,
                },
                "server_time": int(time.time() * 1000),
                "heartbeat_interval_ms": HEARTBEAT_INTERVAL_SECONDS * 1000,
            },
        )
        log_websocket_event(
            "connect", client_count=len(self.connected_clients), client_id=session.user_id
        )
        return True

    async def _send_auth_failure(
        self, session: ClientSession, code: str, message: str
    ) -> None:
        await self._send(session, self._auth_error_payload(code, message))
        await self._close(session.websocket, WS_CLOSE_AUTH_FAILED, message)

    async def _replace_existing_session(self, session: ClientSession, user: Any) -> None:
        user_id = str(getattr(user, "id", ""))
        existing = self.sessions.get(user_id)
        if existing is None or existing is session:
            return
        logger.warning(
            "用户 %s 重复连接，关闭旧连接（user_id=%s）",
            getattr(user, "username", ""),
            user_id,
        )
        await self._cleanup_session(existing, reason="replaced")
        await self._close(existing.websocket, WS_CLOSE_REPLACED, "replaced by new connection")

    # ------------------------------------------------------------------ 消息分发
    async def process_message(self, session: ClientSession, raw_message: Any) -> None:
        """解析并处理一条客户端消息；任何异常都不应让连接循环崩溃。"""
        if isinstance(raw_message, (bytes, bytearray)):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError:
                await self._send_error(session, "INVALID_JSON", "消息必须是 UTF-8 JSON")
                return

        try:
            data = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            await self._send_error(session, "INVALID_JSON", "无效的JSON格式")
            return

        if not isinstance(data, dict):
            await self._send_error(session, "INVALID_PAYLOAD", "消息必须是 JSON 对象")
            return

        message_type = data.get("type")

        if not session.authenticated:
            if message_type == "auth":
                await self.authenticate(session, data.get("token"))
                return
            if message_type == "authenticate":
                # B2b 起旧的自报用户名认证协议被移除
                await self._send_auth_failure(
                    session,
                    "AUTH_INVALID",
                    "认证协议已升级，请使用 auth + token",
                )
                return
            await self._send_error(session, "NOT_AUTHENTICATED", "请先完成认证")
            return

        if message_type == "auth":
            await self._send_error(session, "ALREADY_AUTHENTICATED", "已认证，请勿重复认证")
        elif message_type == "join_room":
            await self._handle_join_room(session, data)
        elif message_type == "leave_room":
            await self._handle_leave_room(session, reply=True)
        elif message_type == "ping":
            await self._send(session, {"type": "pong"})
        else:
            await self._send_error(
                session,
                "UNSUPPORTED_MESSAGE_TYPE",
                f"当前版本不支持消息类型: {message_type}",
            )

    # ------------------------------------------------------------------ 房间频道
    @staticmethod
    def _user_payload(user: Any) -> Dict[str, Any]:
        return {
            "id": str(getattr(user, "id", "")),
            "username": getattr(user, "username", ""),
        }

    def room_online_user_ids(self, room_id: str) -> Set[str]:
        return {
            session.user_id
            for session in self.sessions.values()
            if session.room_id == room_id and session.user_id is not None
        }

    def _members_payload(self, members, room_id: str):
        online_ids = self.room_online_user_ids(room_id)
        payload = []
        for member in members:
            member_id = str(member.get("id", ""))
            payload.append(
                {
                    "id": member_id,
                    "username": member.get("username"),
                    "role": member.get("role", "member"),
                    "online": member_id in online_ids,
                }
            )
        return payload

    @staticmethod
    def _user_event(
        event_type: str,
        room_id: str,
        room_code: Optional[str],
        user: Any,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": event_type,
            "room_id": str(room_id),
            "room_code": room_code,
            "user": WebSocketManager._user_payload(user),
            "ts": int(time.time() * 1000),
        }
        if reason:
            payload["reason"] = reason
        return payload

    async def _handle_join_room(self, session: ClientSession, data: Dict[str, Any]) -> None:
        room_ref = data.get("room_id_or_code") or data.get("room_code")
        if not isinstance(room_ref, str) or not room_ref.strip():
            await self._send_error(
                session, "INVALID_PAYLOAD", "缺少 room_id_or_code"
            )
            return

        try:
            room, members = await rooms_service.get_room_for_member(
                room_ref.strip(), session.user
            )
        except RoomError as exc:
            await self._send_error(session, exc.error_code, exc.message)
            return
        except Exception as exc:  # pragma: no cover - 防御性
            log_exception(exc, "WS 加入房间失败", {"room": room_ref})
            await self._send_error(session, "INTERNAL_ERROR", "加入房间失败")
            return

        if session.room_id == room.id:
            # 幂等：重复 join 只补发当前房间状态，不重复广播
            await self._send_room_joined(session, room, members)
            return

        if session.room_id:
            await self._handle_leave_room(session, reply=False)

        session.room_id = room.id
        session.room_code = room.code

        await self._send_room_joined(session, room, members)
        await self.broadcast_to_room(
            room.id,
            self._user_event(
                "user_joined", room.id, room.code, session.user
            ),
            exclude_user_id=session.user_id,
        )

    async def _send_room_joined(self, session, room, members) -> None:
        room_payload = rooms_service.room_to_dict(room, len(members))
        await self._send(
            session,
            {
                "type": "room_joined",
                "room": room_payload,
                "members": self._members_payload(members, room.id),
                "ts": int(time.time() * 1000),
            },
        )

    async def _handle_leave_room(self, session: ClientSession, reply: bool = True) -> None:
        room_id = session.room_id
        room_code = session.room_code
        if room_id is None:
            if reply:
                await self._send(session, {"type": "left_room", "room_id": None})
            return

        session.room_id = None
        session.room_code = None
        await self.broadcast_to_room(
            room_id,
            self._user_event(
                "user_left", room_id, room_code, session.user, reason="left"
            ),
            exclude_user_id=session.user_id,
        )
        if reply:
            await self._send(
                session,
                {
                    "type": "left_room",
                    "room_id": str(room_id),
                    "room_code": room_code,
                },
            )

    # ------------------------------------------------------------------ 广播/清理
    async def broadcast(self, message: str) -> None:
        """广播消息给所有已连接的客户端（保留给心跳/数据推送等）。"""
        if self.connected_clients:
            websockets.broadcast(self.connected_clients, message)

    async def broadcast_to_room(
        self,
        room_id: str,
        payload: Dict[str, Any],
        exclude_user_id: Optional[str] = None,
    ) -> None:
        """向同一房间频道的其他连接发送消息（按 user_id 排除自己）。"""
        message = json.dumps(payload, ensure_ascii=False)
        for session in list(self.sessions.values()):
            if session.room_id != room_id:
                continue
            if exclude_user_id is not None and session.user_id == exclude_user_id:
                continue
            try:
                await session.websocket.send(message)
            except Exception as exc:
                logger.debug("房间广播发送失败: %s", exc)

    async def _cleanup_session(
        self, session: ClientSession, reason: str = "disconnected"
    ) -> None:
        """清理连接状态：退出房间频道、广播 user_left、移除映射（幂等）。"""
        room_id = session.room_id
        room_code = session.room_code
        user = session.user

        if room_id is not None and user is not None:
            await self.broadcast_to_room(
                room_id,
                self._user_event("user_left", room_id, room_code, user, reason=reason),
                exclude_user_id=session.user_id,
            )
        session.room_id = None
        session.room_code = None

        if session.user_id is not None and self.sessions.get(session.user_id) is session:
            del self.sessions[session.user_id]

        username = getattr(user, "username", None) if user is not None else None
        if username and self.user_websocket_map.get(username) is session.websocket:
            del self.user_websocket_map[username]

        self.connected_clients.discard(session.websocket)
        if user is not None:
            log_websocket_event(
                "disconnect",
                client_count=len(self.connected_clients),
                client_id=session.user_id,
                details=f"WebSocket连接断开({reason})",
            )
        logger.info(
            "客户端断开，当前连接: %s, 用户: %s",
            len(self.connected_clients),
            username or "未认证",
        )

    # ------------------------------------------------------------------ 连接生命周期
    async def send_heartbeat(self, websocket: WebSocketServerProtocol) -> None:
        """发送心跳包给指定WebSocket连接（保留原有 30s/格式）。"""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                await websocket.send(json.dumps({
                    "type": "heartbeat",
                    "time": datetime.now().isoformat()
                }))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:  # pragma: no cover
            logger.debug("心跳发送结束: %s", exc)

    async def handle_client(self, websocket: WebSocketServerProtocol) -> None:
        """处理单个 WebSocket 客户端连接。"""
        session = ClientSession(websocket=websocket)
        self.connected_clients.add(websocket)
        heartbeat_task = asyncio.create_task(self.send_heartbeat(websocket))

        try:
            async for message in websocket:
                try:
                    await self.process_message(session, message)
                except Exception as exc:  # pragma: no cover - 防御性
                    log_exception(exc, "WebSocket消息处理失败")
                    await self._send_error(session, "INTERNAL_ERROR", "消息处理失败")
        except websockets.exceptions.ConnectionClosed as exc:
            logger.info("连接关闭: %s", exc)
        except Exception as exc:  # pragma: no cover
            log_exception(exc, "WebSocket客户端处理异常")
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self._cleanup_session(session, reason="disconnected")

    # ------------------------------------------------------------------ 后台任务
    async def data_pusher(self) -> None:
        """数据推送器，定期向所有客户端推送数据（保留原有行为）。"""
        counter = 0
        while True:
            await asyncio.sleep(15)
            if self.connected_clients:
                counter += 1
                try:
                    await self.broadcast(json.dumps({
                        "type": "push",
                        "data_id": counter,
                        "value": round(20 + 10 * (counter % 10), 2),
                        "timestamp": datetime.now().isoformat()
                    }))
                    logger.info(f"推送数据 #{counter}")
                except Exception as e:
                    log_exception(e, "数据推送失败", {"counter": counter})

    async def inactive_user_checker(self) -> None:
        """定期检查并移除不活跃用户（保留原有行为）。"""
        while True:
            await asyncio.sleep(60)
            try:
                result = await UserService.check_and_remove_inactive_users()
                if result["success"] and result["removed_users"]:
                    logger.info(
                        "离线检测: 移除了 %s 个不活跃用户: %s",
                        len(result["removed_users"]),
                        result["removed_users"],
                    )
                elif result["success"]:
                    logger.debug("离线检测: 没有不活跃用户需要移除")
                else:
                    logger.error("离线检测失败: %s", result["message"])
            except Exception as e:
                log_exception(e, "离线用户检测任务异常")

    async def run_websocket_server(self) -> None:
        """运行 WebSocket 服务器（监听地址可用 WS_HOST/WS_PORT 配置）。

        后台任务（定时推送/离线检测）的唯一启动点是 ``main.py``；本方法只负责
        WebSocket server。重复调用时通过 ``_server_running`` 直接返回。
        """
        if self._server_running:
            logger.warning("WebSocket 服务器已在运行，忽略重复启动")
            return

        host, port = get_ws_bind_address()
        self._server_running = True
        try:
            async with websockets.serve(
                    self.handle_client,
                    host,
                    port,
                    ping_interval=20,
                    ping_timeout=10
            ):
                logger.info(f"WebSocket 服务启动: ws://{host}:{port}")

                await asyncio.Future()
        except Exception as e:
            log_exception(e, "WebSocket服务器启动失败")
            raise
        finally:
            self._server_running = False


# 全局WebSocket管理器实例
websocket_manager = WebSocketManager()
