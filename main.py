import asyncio
import json
from datetime import datetime

import uvicorn
import websockets
from fastapi import Request
from fastapi.responses import JSONResponse

from api.init import create_app
from config.database import init_database, close_database
from log.logger import logger, log_exception

CONNECTED_CLIENTS = set()


# 全局异常处理器
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理中间件"""
    # 记录异常
    log_exception(exc, "全局异常处理", {
        "path": request.url.path,
        "method": request.method,
        "client_ip": request.client.host if request.client else "unknown"
    })

    # 返回标准化的错误响应
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "服务器内部错误",
            "detail": str(exc)
        }
    )


async def register(websocket):
    CONNECTED_CLIENTS.add(websocket)
    logger.info(f"客户端连接，当前在线: {len(CONNECTED_CLIENTS)}")
    await websocket.send(json.dumps({
        "type": "system",
        "message": "连接成功",
        "clients_count": len(CONNECTED_CLIENTS)
    }))


async def unregister(websocket):
    CONNECTED_CLIENTS.discard(websocket)
    logger.info(f"客户端断开，当前在线: {len(CONNECTED_CLIENTS)}")


async def broadcast(message):
    if CONNECTED_CLIENTS:
        websockets.broadcast(CONNECTED_CLIENTS, message)


async def send_heartbeat(websocket):
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
    await register(websocket)
    heartbeat_task = asyncio.create_task(send_heartbeat(websocket))

    try:
        async for message in websocket:
            logger.info(f"收到消息: {message}")

            try:
                data = json.loads(message)

                if data.get("type") == "echo":
                    await websocket.send(json.dumps({
                        "type": "echo",
                        "received": data.get("content"),
                        "time": datetime.now().isoformat()
                    }))

                elif data.get("type") == "broadcast":
                    await broadcast(json.dumps({
                        "type": "broadcast",
                        "from": str(id(websocket))[-6:],
                        "content": data.get("content"),
                        "time": datetime.now().isoformat()
                    }))

                elif data.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))

                else:
                    await websocket.send(json.dumps({
                        "type": "ack",
                        "message": "消息已收到",
                        "your_data": data
                    }))

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


async def run_api_server():
    """运行HTTP API服务器"""
    try:
        # 创建应用并添加全局异常处理
        app = create_app()
        app.add_exception_handler(Exception, global_exception_handler)

        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=8000,
            log_level="info"
        )
        server = uvicorn.Server(config)
        logger.info("HTTP API服务启动: http://localhost:8000")
        await server.serve()
    except Exception as e:
        log_exception(e, "HTTP API服务器启动失败")
        raise


async def init_services():
    """初始化所有服务"""
    try:
        logger.info("服务初始化完成（数据库连接由FastAPI中间件管理）")
    except Exception as e:
        log_exception(e, "服务初始化失败")
        raise


async def cleanup_services():
    """清理服务资源"""
    try:
        logger.info("服务资源清理完成（数据库连接由FastAPI中间件管理）")
    except Exception as e:
        log_exception(e, "服务清理失败")


async def main():
    """同时运行WebSocket和HTTP API服务"""
    try:
        logger.info("Delta Service 启动中...")

        # 初始化服务
        await init_services()

        # 同时启动两个服务
        await asyncio.gather(
            run_websocket_server(),
            run_api_server(),
            data_pusher()
        )
    except Exception as e:
        log_exception(e, "Delta Service 启动失败")
        raise
    finally:
        await cleanup_services()
        logger.info("Delta Service 停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭服务...")
    except Exception as e:
        log_exception(e, "Delta Service 运行异常")
        raise
