import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 创建logs目录（绝对路径）
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 日志文件名（带时间戳）
LOG_FILENAME = f"delta_service_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
LOG_PATH = os.path.join(LOG_DIR, LOG_FILENAME)

# 创建日志记录器
logger = logging.getLogger("delta_service")
logger.setLevel(logging.INFO)

# 避免重复添加处理器
if not logger.handlers:
    # 创建格式化器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 文件处理器（轮转，最大10MB，保留5个备份）
    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # 添加处理器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


# 自定义异常记录函数
def log_exception(exception: Exception, context: str = "", extra_info: dict = None):
    """记录异常信息
    
    Args:
        exception: 异常对象
        context: 异常发生的上下文描述
        extra_info: 额外的上下文信息
    """
    extra = f" - {extra_info}" if extra_info else ""
    logger.error(f"{context}: {str(exception)}{extra}", exc_info=True)


# 自定义业务日志函数
def log_business_event(event_type: str, user_id: str = "", room_code: str = "", details: dict = None):
    """记录业务事件
    
    Args:
        event_type: 事件类型（create_room, join_room, leave_room等）
        user_id: 用户ID
        room_code: 房间码
        details: 详细信息
    """
    details_str = f" - {details}" if details else ""
    logger.info(f"业务事件 - {event_type} - 用户:{user_id} - 房间:{room_code}{details_str}")


# API请求日志函数
def log_api_request(method: str, path: str, status_code: int = None, client_ip: str = "", duration: float = None):
    """记录API请求日志
    
    Args:
        method: HTTP方法
        path: 请求路径
        status_code: 状态码
        client_ip: 客户端IP
        duration: 请求耗时（秒）
    """
    duration_str = f" - 耗时:{duration:.3f}s" if duration is not None else ""
    status_str = f" - 状态:{status_code}" if status_code is not None else ""
    ip_str = f" - IP:{client_ip}" if client_ip else ""

    logger.info(f"API请求 - {method} {path}{status_str}{ip_str}{duration_str}")


# WebSocket连接日志函数
def log_websocket_event(event_type: str, client_count: int = None, client_id: str = "", details: str = ""):
    """记录WebSocket事件日志
    
    Args:
        event_type: 事件类型（connect, disconnect, message等）
        client_count: 当前客户端数量
        client_id: 客户端标识
        details: 详细信息
    """
    count_str = f" - 当前在线:{client_count}" if client_count is not None else ""
    client_str = f" - 客户端:{client_id}" if client_id else ""
    details_str = f" - {details}" if details else ""

    logger.info(f"WebSocket事件 - {event_type}{client_str}{count_str}{details_str}")


# 性能监控日志函数
def log_performance(operation: str, duration: float, size: int = None, user_id: str = ""):
    """记录性能监控日志
    
    Args:
        operation: 操作名称
        duration: 操作耗时（秒）
        size: 数据大小（字节）
        user_id: 用户ID
    """
    size_str = f" - 大小:{size}" if size is not None else ""
    user_str = f" - 用户:{user_id}" if user_id else ""
    logger.info(f"性能监控 - {operation} - 耗时:{duration:.3f}s{size_str}{user_str}")


# SQL 参数脱敏 ---------------------------------------------------------------
# SQL 文本命中这些标记时，绑定参数可能包含明文口令/令牌，必须整体打码。
_SENSITIVE_SQL_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "credential",
    "api_key",
    "apikey",
)

SQL_REDACTED = "***REDACTED***"


def redact_sql_params(sql: str, params: Any = None) -> Any:
    """返回可安全写入日志的 SQL 绑定参数。

    - params 为 None 时返回 None（保持原有“无参数”语义）；
    - SQL 文本（大小写不敏感）命中 password/passwd/pwd/token/secret/
      credential/api_key 等敏感标记时，返回 ``"***REDACTED***"``；
    - 其余情况原样返回 params（不改变调用方数据结构）。

    该函数为纯函数，不依赖日志运行期状态，便于单元测试。
    """
    if params is None:
        return None

    sql_text = sql if isinstance(sql, str) else str(sql)
    if any(marker in sql_text.lower() for marker in _SENSITIVE_SQL_MARKERS):
        return SQL_REDACTED
    return params


# SQL执行日志函数
def log_sql_execution(sql: str, params: Any = None, duration: float = None, success: bool = True, error: str = ""):
    """记录SQL执行日志
    
    Args:
        sql: SQL语句
        params: 参数
        duration: 执行耗时（秒）
        success: 是否成功
        error: 错误信息
    """
    status = "成功" if success else "失败"
    duration_str = f" - 耗时:{duration:.3f}s" if duration is not None else ""
    # 统一脱敏：含口令/令牌字段的 SQL 不记录任何绑定参数值。
    params_str = f" - 参数:{redact_sql_params(sql, params)}" if params else ""
    error_str = f" - 错误:{error}" if error else ""

    # 简化SQL显示，避免日志过长
    sql_short = sql[:100] + "..." if len(sql) > 100 else sql

    logger.info(f"SQL执行 - {status} - {sql_short}{params_str}{duration_str}{error_str}")


# API响应日志函数
def log_api_response(method: str, path: str, status_code: int, response_size: int = None, duration: float = None):
    """记录API响应日志
    
    Args:
        method: HTTP方法
        path: 请求路径
        status_code: 状态码
        response_size: 响应大小（字节）
        duration: 请求耗时（秒）
    """
    size_str = f" - 响应大小:{response_size}字节" if response_size is not None else ""
    duration_str = f" - 耗时:{duration:.3f}s" if duration is not None else ""

    logger.info(f"API响应 - {method} {path} - 状态:{status_code}{size_str}{duration_str}")


# 全局系统日志函数
def log_system_event(event_type: str, component: str = "", details: str = "", level: str = "INFO"):
    """记录系统事件日志
    
    Args:
        event_type: 事件类型（startup, shutdown, config_change等）
        component: 组件名称
        details: 详细信息
        level: 日志级别（INFO, WARNING, ERROR）
    """
    component_str = f" - 组件:{component}" if component else ""
    details_str = f" - {details}" if details else ""

    log_method = getattr(logger, level.lower(), logger.info)
    log_method(f"系统事件 - {event_type}{component_str}{details_str}")


# 数据库连接日志函数
def log_database_connection(event: str, database: str = "", details: str = "", success: bool = True):
    """记录数据库连接日志
    
    Args:
        event: 事件类型（connect, disconnect, reconnect等）
        database: 数据库名称
        details: 详细信息
        success: 是否成功
    """
    status = "成功" if success else "失败"
    db_str = f" - 数据库:{database}" if database else ""
    details_str = f" - {details}" if details else ""

    level = "INFO" if success else "ERROR"
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(f"数据库连接 - {event} - {status}{db_str}{details_str}")


# 请求追踪上下文类
class RequestContext:
    """请求追踪上下文，用于记录请求的完整生命周期"""

    def __init__(self, request_id: str = None):
        self.request_id = request_id or f"req_{int(time.time() * 1000)}"
        self.start_time = time.time()
        self.steps = []

    def add_step(self, step_name: str, details: str = ""):
        """添加处理步骤"""
        step_time = time.time() - self.start_time
        self.steps.append({
            "step": step_name,
            "time": step_time,
            "details": details
        })

    def log_completion(self, success: bool = True, final_details: str = ""):
        """记录请求完成日志"""
        total_time = time.time() - self.start_time
        status = "成功" if success else "失败"

        steps_str = "; ".join([f"{s['step']}({s['time']:.3f}s)" for s in self.steps])

        logger.info(
            f"请求追踪 - {self.request_id} - {status} - 总耗时:{total_time:.3f}s - 步骤:[{steps_str}] - {final_details}")


# 全局日志管理器类
class GlobalLogger:
    """全局日志管理器，提供统一的日志接口"""

    @staticmethod
    def startup():
        """记录系统启动日志"""
        log_system_event("startup", "system", "Delta Service 启动", "INFO")

    @staticmethod
    def shutdown():
        """记录系统关闭日志"""
        log_system_event("shutdown", "system", "Delta Service 关闭", "INFO")

    @staticmethod
    def api_call(method: str, path: str, client_ip: str = ""):
        """记录API调用开始"""
        log_api_request(method, path, None, client_ip)
        return RequestContext()

    @staticmethod
    def api_response(context: RequestContext, method: str, path: str, status_code: int, response_size: int = None):
        """记录API响应完成"""
        duration = time.time() - context.start_time
        log_api_response(method, path, status_code, response_size, duration)
        context.log_completion(status_code < 400, f"状态码:{status_code}")

    @staticmethod
    def sql_query(sql: str, params: Any = None):
        """记录SQL查询开始"""
        start_time = time.time()
        return {
            "sql": sql,
            "params": params,
            "start_time": start_time
        }

    @staticmethod
    def sql_result(context: dict, success: bool = True, error: str = ""):
        """记录SQL查询结果"""
        duration = time.time() - context["start_time"]
        log_sql_execution(context["sql"], context["params"], duration, success, error)

    @staticmethod
    def websocket_connect(client_id: str, client_count: int):
        """记录WebSocket连接"""
        log_websocket_event("connect", client_count, client_id)

    @staticmethod
    def websocket_disconnect(client_id: str, client_count: int):
        """记录WebSocket断开"""
        log_websocket_event("disconnect", client_count, client_id)

    @staticmethod
    def database_connect(database: str, success: bool = True, details: str = ""):
        """记录数据库连接"""
        log_database_connection("connect", database, details, success)

    @staticmethod
    def database_disconnect(database: str, success: bool = True, details: str = ""):
        """记录数据库断开"""
        log_database_connection("disconnect", database, details, success)
