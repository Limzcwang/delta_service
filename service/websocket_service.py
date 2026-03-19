"""WebSocket服务层（业务逻辑层）"""

import json
from datetime import datetime
from typing import Optional, Dict, Any
from websockets.server import WebSocketServerProtocol

from log.logger import logger, log_exception, log_business_event
from service.user import UserService
from service.room import (
    get_room_info, join_room, leave_room,
    RoomNotFoundError, UserNotInRoomError
)


class WebSocketService:
    """WebSocket服务层"""
    
    def __init__(self):
        self.authenticated_user: Optional[str] = None
        self.current_room: Optional[str] = None
        self.user_sessions: Dict[str, Dict[str, Any]] = {}
    
    def get_authenticated_user(self) -> Optional[str]:
        """获取当前认证用户"""
        return self.authenticated_user
    
    def get_current_room(self) -> Optional[str]:
        """获取当前房间"""
        return self.current_room
    
    async def handle_message(self, data: Dict[str, Any], websocket: WebSocketServerProtocol,
                           authenticated_user: Optional[str], current_room: Optional[str],
                           websocket_manager) -> None:
        """处理WebSocket消息
        
        Args:
            data: 解析后的消息数据
            websocket: WebSocket连接对象
            authenticated_user: 当前认证用户
            current_room: 当前房间
            websocket_manager: WebSocket管理器实例
        """
        self.authenticated_user = authenticated_user
        self.current_room = current_room
        
        message_type = data.get("type")
        
        if message_type == "authenticate":
            await self.handle_authentication(data, websocket, websocket_manager)
        elif message_type == "join_room":
            await self.handle_join_room(data, websocket, websocket_manager)
        elif message_type == "leave_room":
            await self.handle_leave_room(data, websocket, websocket_manager)
        elif message_type == "room_message":
            await self.handle_room_message(data, websocket, websocket_manager)
        elif message_type == "echo":
            await self.handle_echo(data, websocket)
        elif message_type == "broadcast":
            await self.handle_broadcast(data, websocket, websocket_manager)
        elif message_type == "ping":
            await self.handle_ping(websocket)
        elif message_type == "activity":
            await self.handle_activity(data, websocket)
        else:
            await self.handle_default_message(data, websocket)
    
    async def handle_authentication(self, data: Dict[str, Any], websocket: WebSocketServerProtocol,
                                  websocket_manager) -> None:
        """处理用户身份验证"""
        username = data.get("username")
        if not username:
            await websocket.send(json.dumps({
                "type": "auth_failed",
                "message": "用户名不能为空"
            }))
            return
        
        try:
            # 检查用户是否已登录
            check_result = await UserService.check_user_online(username)
            if check_result.get("online"):
                self.authenticated_user = username
                await websocket_manager.register(websocket, username)
                
                # 记录业务事件
                log_business_event("websocket_auth", username, details={
                    "action": "认证成功",
                    "websocket_id": str(id(websocket))[-6:]
                })
                
                await websocket.send(json.dumps({
                    "type": "auth_success",
                    "message": "身份验证成功",
                    "username": username,
                    "timestamp": datetime.now().isoformat()
                }))
            else:
                await websocket.send(json.dumps({
                    "type": "auth_failed",
                    "message": "用户未登录或已离线"
                }))
        except Exception as e:
            log_exception(e, "WebSocket认证失败", {"username": username})
            await websocket.send(json.dumps({
                "type": "auth_failed",
                "message": "认证过程发生错误"
            }))
    
    async def handle_join_room(self, data: Dict[str, Any], websocket: WebSocketServerProtocol,
                              websocket_manager) -> None:
        """处理用户加入房间"""
        if not self.authenticated_user:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "请先进行身份验证"
            }))
            return
        
        room_code = data.get("room_code")
        if not room_code:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "房间码不能为空"
            }))
            return
        
        try:
            # 加入房间
            join_room(self.authenticated_user, room_code)
            self.current_room = room_code
            
            # 获取房间信息
            room_info = get_room_info(room_code)
            
            # 记录业务事件
            log_business_event("join_room", self.authenticated_user, room_code, {
                "action": "加入房间",
                "room_domain": room_info.get("domain"),
                "online_count": len(room_info.get("online", []))
            })
            
            # 向房间内其他用户广播加入消息
            await websocket_manager.broadcast_to_room(room_code, json.dumps({
                "type": "user_joined",
                "username": self.authenticated_user,
                "room_code": room_code,
                "message": f"用户 {self.authenticated_user} 加入了房间",
                "timestamp": datetime.now().isoformat()
            }), exclude_user=self.authenticated_user)
            
            # 发送成功响应给当前用户
            await websocket.send(json.dumps({
                "type": "join_success",
                "message": "加入房间成功",
                "room_code": room_code,
                "room_info": {
                    "domain": room_info.get("domain"),
                    "creator": room_info.get("creator"),
                    "members_count": len(room_info.get("members", [])),
                    "online_count": len(room_info.get("online", [])),
                    "created_at": room_info.get("created_at")
                },
                "timestamp": datetime.now().isoformat()
            }))
            
        except RoomNotFoundError:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"房间 {room_code} 不存在"
            }))
        except Exception as e:
            log_exception(e, "加入房间失败", {
                "username": self.authenticated_user,
                "room_code": room_code
            })
            await websocket.send(json.dumps({
                "type": "error",
                "message": "加入房间失败"
            }))
    
    async def handle_leave_room(self, data: Dict[str, Any], websocket: WebSocketServerProtocol,
                               websocket_manager) -> None:
        """处理用户离开房间"""
        if not self.authenticated_user:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "请先进行身份验证"
            }))
            return
        
        room_code = data.get("room_code", self.current_room)
        if not room_code:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "未指定房间码"
            }))
            return
        
        try:
            # 离开房间
            leave_room(self.authenticated_user, room_code)
            
            # 记录业务事件
            log_business_event("leave_room", self.authenticated_user, room_code, {
                "action": "离开房间"
            })
            
            # 向房间内其他用户广播离开消息
            await websocket_manager.broadcast_to_room(room_code, json.dumps({
                "type": "user_left",
                "username": self.authenticated_user,
                "room_code": room_code,
                "message": f"用户 {self.authenticated_user} 离开了房间",
                "timestamp": datetime.now().isoformat()
            }), exclude_user=self.authenticated_user)
            
            # 清除当前房间信息
            self.current_room = None
            
            # 发送成功响应
            await websocket.send(json.dumps({
                "type": "leave_success",
                "message": "离开房间成功",
                "room_code": room_code,
                "timestamp": datetime.now().isoformat()
            }))
            
        except (RoomNotFoundError, UserNotInRoomError) as e:
            await websocket.send(json.dumps({
                "type": "error",
                "message": str(e)
            }))
        except Exception as e:
            log_exception(e, "离开房间失败", {
                "username": self.authenticated_user,
                "room_code": room_code
            })
            await websocket.send(json.dumps({
                "type": "error",
                "message": "离开房间失败"
            }))
    
    async def handle_room_message(self, data: Dict[str, Any], websocket: WebSocketServerProtocol,
                                 websocket_manager) -> None:
        """处理房间内消息发送"""
        if not self.authenticated_user:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "请先进行身份验证"
            }))
            return
        
        if not self.current_room:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "请先加入房间"
            }))
            return
        
        message_content = data.get("content")
        if not message_content:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "消息内容不能为空"
            }))
            return
        
        try:
            # 构建房间消息
            room_message = json.dumps({
                "type": "room_message",
                "username": self.authenticated_user,
                "room_code": self.current_room,
                "content": message_content,
                "timestamp": datetime.now().isoformat()
            })
            
            # 向房间内所有用户广播消息（包括发送者自己）
            await websocket_manager.broadcast_to_room(self.current_room, room_message)
            
            # 记录业务事件
            log_business_event("send_room_message", self.authenticated_user, self.current_room, {
                "action": "发送房间消息",
                "message_length": len(message_content)
            })
            
            # 发送确认消息给发送者
            await websocket.send(json.dumps({
                "type": "message_sent",
                "message": "消息发送成功",
                "timestamp": datetime.now().isoformat()
            }))
            
        except Exception as e:
            log_exception(e, "发送房间消息失败", {
                "username": self.authenticated_user,
                "room_code": self.current_room,
                "message_content": message_content
            })
            await websocket.send(json.dumps({
                "type": "error",
                "message": "消息发送失败"
            }))
    
    async def handle_echo(self, data: Dict[str, Any], websocket: WebSocketServerProtocol) -> None:
        """处理回显消息"""
        content = data.get("content", "")
        await websocket.send(json.dumps({
            "type": "echo",
            "received": content,
            "time": datetime.now().isoformat()
        }))
    
    async def handle_broadcast(self, data: Dict[str, Any], websocket: WebSocketServerProtocol,
                              websocket_manager) -> None:
        """处理广播消息"""
        content = data.get("content", "")
        from_user = self.authenticated_user or str(id(websocket))[-6:]
        
        broadcast_message = json.dumps({
            "type": "broadcast",
            "from": from_user,
            "content": content,
            "time": datetime.now().isoformat()
        })
        
        await websocket_manager.broadcast(broadcast_message)
    
    async def handle_ping(self, websocket: WebSocketServerProtocol) -> None:
        """处理心跳包"""
        await websocket.send(json.dumps({"type": "pong"}))
    
    async def handle_activity(self, data: Dict[str, Any], websocket: WebSocketServerProtocol) -> None:
        """处理用户活动心跳"""
        if self.authenticated_user:
            await UserService.update_user_activity(self.authenticated_user)
            await websocket.send(json.dumps({
                "type": "activity_ack",
                "message": "活动时间已更新"
            }))
    
    async def handle_default_message(self, data: Dict[str, Any], websocket: WebSocketServerProtocol) -> None:
        """处理默认消息类型"""
        await websocket.send(json.dumps({
            "type": "ack",
            "message": "消息已收到",
            "your_data": data
        }))