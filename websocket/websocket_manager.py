"""WebSocket连接管理器"""

import asyncio
import json
from datetime import datetime

import websockets

from log.logger import logger, log_exception
from service.user import UserService

CONNECTED_CLIENTS = set()
USER_WEBSOCKET_MAP = {}  # 用户到WebSocket的映射


async def register(websocket, username: str = None):
    """注册WebSocket连接"""
    CONNECTED_CLIENTS.add(websocket)
    
    if username:
        # 关联WebSocket到用户
        USER_WEBSOCKET_MAP[username] = websocket
        
        # 更新用户在线状态
        await UserService.update_user_activity(username)
        
        # 更新用户WebSocket连接信息
        for online_user in UserService.USERS_ONLINE:
            if online_user.get("username") == username:
                online_user["websocket"] = websocket
                break
    
    logger.info(f"客户端连接，当前在线: {len(CONNECTED_CLIENTS)}, 用户: {username or '未认证'}")
    await websocket.send(json.dumps({
        "type": "system",
        "message": "连接成功",
        "clients_count": len(CONNECTED_CLIENTS),
        "authenticated": username is not None
    }))


async def unregister(websocket):
    """注销WebSocket连接"""
    CONNECTED_CLIENTS.discard(websocket)
    
    # 从用户映射中移除
    username_to_remove = None
    for username, ws in USER_WEBSOCKET_MAP.items():
        if ws == websocket:
            username_to_remove = username
            break
    
    if username_to_remove:
        del USER_WEBSOCKET_MAP[username_to_remove]
        
        # 更新用户WebSocket连接信息
        for online_user in UserService.USERS_ONLINE:
            if online_user.get("username") == username_to_remove:
                online_user["websocket"] = None
                break
    
    logger.info(f"客户端断开，当前在线: {len(CONNECTED_CLIENTS)}, 用户: {username_to_remove or '未认证'}")


async def broadcast(message):
    """广播消息给所有连接的客户端"""
    if CONNECTED_CLIENTS:
        websockets.broadcast(CONNECTED_CLIENTS, message)


async def send_heartbeat(websocket):
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


async def handle_client(websocket):
    """处理单个WebSocket客户端连接"""
    await register(websocket)
    heartbeat_task = asyncio.create_task(send_heartbeat(websocket))
    
    # 用户身份验证状态
    authenticated_user = None

    try:
        async for message in websocket:
            logger.info(f"收到消息: {message}")

            try:
                data = json.loads(message)

                if data.get("type") == "authenticate":
                    # 用户身份验证
                    username = data.get("username")
                    if username:
                        # 检查用户是否已登录
                        check_result = await UserService.check_user_online(username)
                        if check_result.get("online"):
                            authenticated_user = username
                            await register(websocket, username)
                            await websocket.send(json.dumps({
                                "type": "auth_success",
                                "message": "身份验证成功",
                                "username": username
                            }))
                        else:
                            await websocket.send(json.dumps({
                                "type": "auth_failed",
                                "message": "用户未登录或已离线"
                            }))

                elif data.get("type") == "echo":
                    await websocket.send(json.dumps({
                        "type": "echo",
                        "received": data.get("content"),
                        "time": datetime.now().isoformat()
                    }))

                elif data.get("type") == "broadcast":
                    await broadcast(json.dumps({
                        "type": "broadcast",
                        "from": authenticated_user or str(id(websocket))[-6:],
                        "content": data.get("content"),
                        "time": datetime.now().isoformat()
                    }))

                elif data.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))

                elif data.get("type") == "activity":
                    # 用户活动心跳
                    if authenticated_user:
                        await UserService.update_user_activity(authenticated_user)
                        await websocket.send(json.dumps({
                            "type": "activity_ack",
                            "message": "活动时间已更新"
                        }))

                else:
                    await websocket.send(json.dumps({
                        "type": "ack",
                        "message": "消息已收到",
                        "your_data": data
                    }))

                # 更新已认证用户的活动时间
                if authenticated_user:
                    await UserService.update_user_activity(authenticated_user)

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
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await unregister(websocket)


async def data_pusher():
    """数据推送器，定期向所有客户端推送数据"""
    counter = 0
    while True:
        await asyncio.sleep(15)
        if CONNECTED_CLIENTS:
            counter += 1
            try:
                await broadcast(json.dumps({
                    "type": "push",
                    "data_id": counter,
                    "value": round(20 + 10 * (counter % 10), 2),
                    "timestamp": datetime.now().isoformat()
                }))
                logger.info(f"推送数据 #{counter}")
            except Exception as e:
                log_exception(e, "数据推送失败", {"counter": counter})


async def inactive_user_checker():
    """定期检查并移除不活跃用户"""
    while True:
        await asyncio.sleep(60)  # 每分钟检查一次
        try:
            result = await UserService.check_and_remove_inactive_users()
            if result["success"] and result["removed_users"]:
                logger.info(f"离线检测: 移除了 {len(result['removed_users'])} 个不活跃用户: {result['removed_users']}")
            elif result["success"]:
                logger.debug("离线检测: 没有不活跃用户需要移除")
            else:
                logger.error(f"离线检测失败: {result['message']}")
        except Exception as e:
            log_exception(e, "离线用户检测任务异常")


async def run_websocket_server():
    """运行WebSocket服务器"""
    try:
        async with websockets.serve(
                handle_client,
                "localhost",
                8765,
                ping_interval=20,
                ping_timeout=10
        ):
            logger.info("WebSocket 服务启动: ws://localhost:8765")
            await asyncio.Future()  # 运行直到被取消
    except Exception as e:
        log_exception(e, "WebSocket服务器启动失败")
        raise