"""API请求响应日志中间件"""

import time
from typing import Callable, Dict, Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .logger import GlobalLogger, RequestContext


class APILoggingMiddleware(BaseHTTPMiddleware):
    """API请求响应日志中间件"""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.enabled = True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求并记录日志
        
        Args:
            request: 请求对象
            call_next: 下一个中间件或路由处理函数
            
        Returns:
            响应对象
        """
        if not self.enabled:
            return await call_next(request)

        # 跳过健康检查等不需要记录的路径
        if request.url.path in ['/health', '/favicon.ico', '/docs', '/redoc', '/openapi.json']:
            return await call_next(request)

        # 创建请求上下文
        request_context = GlobalLogger.api_call(
            request.method,
            str(request.url.path),
            client_ip=request.client.host if request.client else "unknown"
        )

        # 记录请求开始
        request_context.add_step("请求接收", f"方法:{request.method} 路径:{request.url.path}")

        start_time = time.time()
        response = None

        try:
            # 调用下一个中间件或路由
            response = await call_next(request)

            # 记录处理完成
            request_context.add_step("请求处理", f"状态码:{response.status_code}")

            # 计算请求耗时
            duration = time.time() - start_time

            # 记录API响应
            GlobalLogger.api_response(
                request_context,
                request.method,
                str(request.url.path),
                response.status_code,
                response.headers.get("content-length", 0)
            )

            return response

        except Exception as e:
            # 记录异常情况
            duration = time.time() - start_time
            request_context.add_step("请求异常", f"异常:{str(e)}")

            # 记录失败的API响应
            GlobalLogger.api_response(
                request_context,
                request.method,
                str(request.url.path),
                500,  # 内部服务器错误
                None,
                duration
            )

            raise


class LoggingRoute(APIRoute):
    """自定义路由类，用于记录路由级别的详细信息"""

    def get_route_handler(self) -> Callable:
        """获取路由处理器，添加日志记录功能"""
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            # 记录路由处理开始
            route_context = RequestContext()
            route_context.add_step("路由匹配", f"路径:{self.path} 方法:{self.methods}")

            try:
                # 执行原始路由处理器
                response = await original_route_handler(request)

                # 记录路由处理完成
                route_context.add_step("路由处理完成", f"状态码:{response.status_code}")
                route_context.log_completion(True, f"路由:{self.name}")

                return response

            except Exception as e:
                # 记录路由处理异常
                route_context.add_step("路由处理异常", f"异常:{str(e)}")
                route_context.log_completion(False, f"路由:{self.name} - 异常:{str(e)}")
                raise

        return custom_route_handler


# API请求统计类
class APIStatistics:
    """API请求统计"""

    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
        self.requests_by_method: Dict[str, int] = {}
        self.requests_by_endpoint: Dict[str, int] = {}
        self.response_codes: Dict[int, int] = {}
        self.slow_requests = []  # 记录慢请求

    def add_request(self, method: str, endpoint: str, status_code: int, duration: float):
        """添加请求统计
        
        Args:
            method: HTTP方法
            endpoint: 端点路径
            status_code: 状态码
            duration: 请求耗时
        """
        self.total_requests += 1
        self.total_response_time += duration

        # 按方法统计
        self.requests_by_method[method] = self.requests_by_method.get(method, 0) + 1

        # 按端点统计
        self.requests_by_endpoint[endpoint] = self.requests_by_endpoint.get(endpoint, 0) + 1

        # 按状态码统计
        self.response_codes[status_code] = self.response_codes.get(status_code, 0) + 1

        # 判断成功或失败
        if 200 <= status_code < 400:
            self.successful_requests += 1
        else:
            self.failed_requests += 1

        # 记录慢请求（超过3秒）
        if duration > 3.0:
            self.slow_requests.append({
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "duration": duration,
                "timestamp": time.time()
            })

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息
        
        Returns:
            统计信息字典
        """
        avg_time = self.total_response_time / self.total_requests if self.total_requests > 0 else 0

        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.successful_requests / self.total_requests if self.total_requests > 0 else 0,
            "total_response_time": self.total_response_time,
            "average_response_time": avg_time,
            "requests_by_method": self.requests_by_method,
            "requests_by_endpoint": self.requests_by_endpoint,
            "response_codes": self.response_codes,
            "slow_request_count": len(self.slow_requests),
            "slow_requests": self.slow_requests[-10:]  # 返回最近10个慢请求
        }


# 全局API统计实例
api_statistics = APIStatistics()


# API日志配置类
class APILoggingConfig:
    """API日志配置"""

    def __init__(self):
        self.enable_request_logging = True
        self.enable_response_logging = True
        self.enable_route_logging = True
        self.slow_request_threshold = 3.0  # 慢请求阈值（秒）
        self.excluded_paths = ['/health', '/favicon.ico', '/docs', '/redoc', '/openapi.json']

    def is_path_excluded(self, path: str) -> bool:
        """检查路径是否被排除
        
        Args:
            path: 请求路径
            
        Returns:
            是否被排除
        """
        return any(path.startswith(excluded) for excluded in self.excluded_paths)


# 全局API日志配置实例
api_logging_config = APILoggingConfig()
