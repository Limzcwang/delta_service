"""WebSocket连接管理器（表现层）"""

import asyncio
import json
from datetime import datetime
from typing import Set, Dict, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from log.logger import logger, log_exception, log_websocket_event
from service.room import get_room_info
from service.user import UserService
from service.websocket_service import WebSocketService


class WebSocketManager:
    """WebSocket连接管理器（表现层）"""

    def __init__(self):
        self.connected_clients: Set[WebSocketServerProtocol] = set()
        self.user_websocket_map: Dict[str, WebSocketServerProtocol] = {}
        self.websocket_service = WebSocketService()

    async def register(self, websocket: WebSocketServerProtocol, username: str = None) -> None:
        """注册WebSocket连接"""
        self.connected_clients.add(websocket)

        if username:
            # 关联WebSocket到用户
            self.user_websocket_map[username] = websocket

            # 更新用户在线状态
            await UserService.update_user_activity(username)

            # 更新用户WebSocket连接信息
            result = await UserService.check_user_online(username)
            if result.get("online"):
                await UserService.update_user_websocket(username, websocket)

        logger.info(f"客户端连接，当前在线: {len(self.connected_clients)}, 用户: {username or '未认证'}")

        # 发送连接成功消息
        await websocket.send(json.dumps({
            "type": "system",
            "message": "连接成功",
            "clients_count": len(self.connected_clients),
            "authenticated": username is not None,
            "timestamp": datetime.now().isoformat()
        }))

    async def unregister(self, websocket: WebSocketServerProtocol) -> None:
        """注销WebSocket连接"""
        self.connected_clients.discard(websocket)

        # 从用户映射中移除
        username_to_remove = None
        for username, ws in self.user_websocket_map.items():
            if ws == websocket:
                username_to_remove = username
                break

        if username_to_remove:
            del self.user_websocket_map[username_to_remove]

            # 更新用户WebSocket连接信息
            await UserService.update_user_websocket(username_to_remove, None)

            # 记录用户离线事件
            log_websocket_event("disconnect", client_count=len(self.connected_clients),
                                client_id=username_to_remove, details="WebSocket连接断开")

        logger.info(f"客户端断开，当前在线: {len(self.connected_clients)}, 用户: {username_to_remove or '未认证'}")

    async def broadcast(self, message: str) -> None:
        """广播消息给所有连接的客户端"""
        if self.connected_clients:
            websockets.broadcast(self.connected_clients, message)

    async def broadcast_to_room(self, room_code: str, message: str, exclude_user: str = None) -> None:
        """向房间内所有用户广播消息"""
        try:
            room_info = get_room_info(room_code)
            online_users = room_info.get("online", set())

            for username in online_users:
                if username == exclude_user:
                    continue

                websocket = self.user_websocket_map.get(username)
                if websocket:
                    try:
                        await websocket.send(message)
                    except websockets.exceptions.ConnectionClosed:
                        # 连接已关闭，从映射中移除
                        if username in self.user_websocket_map:
                            del self.user_websocket_map[username]
                            await UserService.update_user_websocket(username, None)

        except Exception as e:
            logger.error(f"房间广播失败: {e}")

    async def send_heartbeat(self, websocket: WebSocketServerProtocol) -> None:
        """发送心跳包给指定WebSocket连接"""
        try:
            while True:
                await asyncio.sleep(30)
                await websocket.send(json.dumps({
                    "type": "heartbeat",
                    "time": datetime.now().isoformat()
                }))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def handle_client(self, websocket: WebSocketServerProtocol) -> None:
        """处理单个WebSocket客户端连接"""
        await self.register(websocket)
        heartbeat_task = asyncio.create_task(self.send_heartbeat(websocket))

        # 用户身份验证状态
        authenticated_user: Optional[str] = None
        current_room: Optional[str] = None

        try:
            async for message in websocket:
                logger.info(f"收到消息: {message}")

                try:
                    data = json.loads(message)

                    # 处理不同类型的消息
                    await self.websocket_service.handle_message(
                        data, websocket, authenticated_user, current_room, self
                    )

                    # 更新消息处理后的状态
                    authenticated_user = self.websocket_service.get_authenticated_user()
                    current_room = self.websocket_service.get_current_room()

                except json.JSONDecodeError as e:
                    logger.error(f"WebSocket消息JSON解析失败: {message}")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "无效的JSON格式",
                        "error_code": "INVALID_JSON"
                    }))
                except Exception as e:
                    log_exception(e, "WebSocket消息处理失败", {"message": message})
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "消息处理失败",
                        "error_code": "MESSAGE_PROCESSING_ERROR"
                    }))

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"连接关闭: {e}")
        except Exception as e:
            log_exception(e, "WebSocket客户端处理异常")
        finally:
            # 用户断开连接时处理
            if authenticated_user:
                # 如果用户在房间中，发送离开通知
                if current_room:
                    try:
                        await self.broadcast_to_room(current_room, json.dumps({
                            "type": "user_left",
                            "username": authenticated_user,
                            "room_code": current_room,
                            "message": f"用户 {authenticated_user} 已离开房间",
                            "timestamp": datetime.now().isoformat()
                        }))
                    except Exception:
                        pass

                # 更新用户在线状态
                await UserService.update_user_websocket(authenticated_user, None)

            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self.unregister(websocket)

    async def data_pusher(self) -> None:
        """数据推送器，定期向所有客户端推送数据"""
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
        """定期检查并移除不活跃用户"""
        while True:
            await asyncio.sleep(60)  # 每分钟检查一次
            try:
                result = await UserService.check_and_remove_inactive_users()
                if result["success"] and result["removed_users"]:
                    logger.info(
                        f"离线检测: 移除了 {len(result['removed_users'])} 个不活跃用户: {result['removed_users']}")
                elif result["success"]:
                    logger.debug("离线检测: 没有不活跃用户需要移除")
                else:
                    logger.error(f"离线检测失败: {result['message']}")
            except Exception as e:
                log_exception(e, "离线用户检测任务异常")

    async def run_websocket_server(self) -> None:
        """运行WebSocket服务器"""
        try:
            async with websockets.serve(
                    self.handle_client,
                    "localhost",
                    8765,
                    ping_interval=20,
                    ping_timeout=10
            ):
                logger.info("WebSocket 服务启动: ws://localhost:8765")

                # 启动后台任务
                asyncio.create_task(self.data_pusher())
                asyncio.create_task(self.inactive_user_checker())

                await asyncio.Future()  # 运行直到被取消
        except Exception as e:
            log_exception(e, "WebSocket服务器启动失败")
            raise


# 全局WebSocket管理器实例
websocket_manager = WebSocketManager()
