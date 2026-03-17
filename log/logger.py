import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime

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
        maxBytes=10*1024*1024,  # 10MB
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