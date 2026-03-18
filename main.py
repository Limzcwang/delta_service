import asyncio
import json
from datetime import datetime

import uvicorn
import websockets
from fastapi import Request
from fastapi.responses import JSONResponse

from api.init import create_app
from config.database import init_database, close_database
from log.logger import logger, log_exception, GlobalLogger
from log.api_logger import APILoggingMiddleware
from log.sql_logger import init_sql_logging
from service.user import UserService
from websocket import (
    data_pusher,
    inactive_user_checker,
    run_websocket_server
)


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
        # 记录系统初始化开始
        GlobalLogger.startup()
        
        # 初始化数据库连接
        await init_database()
        GlobalLogger.database_connect("delta_service", True, "数据库连接初始化成功")
        
        # 初始化SQL日志记录
        await init_sql_logging()
        logger.info("SQL日志记录初始化完成")
        
        logger.info("所有服务初始化完成")
    except Exception as e:
        log_exception(e, "服务初始化失败")
        raise


async def cleanup_services():
    """清理服务资源"""
    try:
        # 关闭数据库连接
        await close_database()
        GlobalLogger.database_disconnect("delta_service", True, "数据库连接正常关闭")
        
        # 记录系统关闭
        GlobalLogger.shutdown()
        
        logger.info("服务资源清理完成")
    except Exception as e:
        log_exception(e, "服务清理失败")


async def main():
    """同时运行WebSocket和HTTP API服务"""
    try:
        logger.info("Delta Service 启动中...")

        # 初始化服务
        await init_services()

        # 同时启动多个服务
        await asyncio.gather(
            run_websocket_server(),
            run_api_server(),
            data_pusher(),
            inactive_user_checker()
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