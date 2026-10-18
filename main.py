import asyncio
import uuid

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

from api.init import create_app
from config.database import init_database, close_database
from log.logger import logger, log_exception, GlobalLogger
from log.sql_logger import init_sql_logging
from websocket import (
    websocket_manager
)


def _resolve_request_id(request: Request) -> str:
    """获取请求的 request_id；没有则生成一个。

    优先复用中间件写入 request.state 的 request_id，便于把全局异常日志
    与请求追踪日志对起来；对没有经过中间件的请求（如测试或直连）兜底生成。
    """
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    if request_id:
        return str(request_id)
    return f"req_{uuid.uuid4().hex[:12]}"


# 全局异常处理器
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理中间件

    安全约定：客户端只拿到通用错误结构和 request_id；异常类型、消息与
    traceback 只写服务端日志（通过 request_id 检索对账）。
    """
    request_id = _resolve_request_id(request)

    # 完整异常（type + message + traceback）只记录到服务端日志。
    log_exception(
        exc,
        f"全局异常处理 - {type(exc).__name__} - request_id:{request_id}",
        {
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else "unknown",
        },
    )

    # 返回标准化的错误响应：不含异常详情、连接串、SQL、堆栈等内部信息。
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "服务器内部错误",
            "request_id": request_id,
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
            websocket_manager.run_websocket_server(),
            run_api_server(),
            websocket_manager.data_pusher(),
            websocket_manager.inactive_user_checker()
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
