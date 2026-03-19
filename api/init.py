from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise

from api.room import router as room_router
from api.user import router as user_router
from config.database import DATABASE_CONFIG
from log.api_logger import APILoggingMiddleware, LoggingRoute
from log.logger import GlobalLogger
from log.sql_logger import init_sql_logging


def create_app():
    """创建FastAPI应用实例"""
    app = FastAPI(
        title="Delta Service API",
        description="实时通信服务的HTTP API接口",
        version="1.0.0"
    )

    # 添加CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境应该限制具体域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 添加API日志中间件
    app.add_middleware(APILoggingMiddleware)

    # 注册Tortoise ORM
    register_tortoise(
        app,
        config=DATABASE_CONFIG,
        generate_schemas=False,  # 已经在init_database中生成
        add_exception_handlers=True,
    )

    # 使用自定义路由类（支持路由级别日志）
    app.router.route_class = LoggingRoute

    # 注册路由
    app.include_router(room_router)
    app.include_router(user_router)

    # 健康检查端点
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "delta_service"}

    # 应用启动事件
    @app.on_event("startup")
    async def startup_event():
        """应用启动时执行"""
        # 记录系统启动日志
        GlobalLogger.startup()

        # 初始化SQL日志记录
        await init_sql_logging()

        print("Delta Service API 启动完成")

    # 应用关闭事件
    @app.on_event("shutdown")
    async def shutdown_event():
        """应用关闭时执行"""
        # 记录系统关闭日志
        GlobalLogger.shutdown()

        print("Delta Service API 已关闭")

    return app
