from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.room import router as room_router
from api.user import router as user_router


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
    
    # 注册路由
    app.include_router(room_router)
    app.include_router(user_router)
    
    # 健康检查端点
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "delta_service"}
    
    return app