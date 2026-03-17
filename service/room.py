import random
import string
import threading
import time

import const.str
from log.logger import logger, log_exception, log_business_event

# 自定义异常类
class RoomServiceError(Exception):
    """房间服务基础异常"""
    def __init__(self, message: str, error_code: str = None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)

class RoomNotFoundError(RoomServiceError):
    """房间不存在异常"""
    def __init__(self, room_code: str):
        super().__init__(f"房间 {room_code} 不存在", "ROOM_NOT_FOUND")

class RoomAlreadyExistsError(RoomServiceError):
    """房间已存在异常"""
    def __init__(self, room_code: str):
        super().__init__(f"房间 {room_code} 已存在", "ROOM_ALREADY_EXISTS")

class UserNotInRoomError(RoomServiceError):
    """用户不在房间异常"""
    def __init__(self, user_id: str, room_code: str):
        super().__init__(f"用户 {user_id} 不在房间 {room_code}", "USER_NOT_IN_ROOM")

class InvalidDomainError(RoomServiceError):
    """无效的房间领域异常"""
    def __init__(self, domain_enum: int):
        super().__init__(f"无效的房间领域: {domain_enum}", "INVALID_DOMAIN")

# 房间管理
ROOM_MANAGER = {}
# 公共房间
ROOM_PUBLIC = []

# 线程锁
room_lock = threading.Lock()


def generate_room_code(length=5):
    """生成包含大写字母和数字的随机字符串"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length))


def get_room_info(room_code: str) -> dict:
    """
    获取房间信息
    
    Args:
        room_code: 房间码
    
    Returns:
        房间信息字典
    
    Raises:
        RoomNotFoundError: 房间不存在时抛出
    """
    if room_code not in ROOM_MANAGER:
        raise RoomNotFoundError(room_code)
    return ROOM_MANAGER[room_code]


def get_room_domain_enum(domain_enum: int) -> str:
    """
    获取房间领域枚举值
    
    Args:
        domain_enum: 1：公共 2：私有
    
    Returns:
        领域字符串
    
    Raises:
        InvalidDomainError: 无效的领域枚举时抛出
    """
    if domain_enum == 1:
        return const.str.PUBLIC_DOMAIN
    elif domain_enum == 2:
        return const.str.PRIVATE_DOMAIN
    else:
        raise InvalidDomainError(domain_enum)


def create_room(user_id: str, max_retries: int = 10, domain_enum: int = 1) -> str:
    """
    创建房间（线程安全）

    Args:
        user_id: 创建者用户ID
        max_retries: 最大重试次数，防止无限循环
        domain_enum: 房间领域枚举
    
    Returns:
        生成的房间码
    
    Raises:
        RoomServiceError: 创建房间失败时抛出
    """
    try:
        for attempt in range(max_retries):
            room_code = generate_room_code()

            # 加锁保护临界区
            with room_lock:
                if room_code not in ROOM_MANAGER:
                    room_domain = get_room_domain_enum(domain_enum)
                    ROOM_MANAGER[room_code] = {
                        "domain": room_domain,
                        "creator": user_id,
                        "members": set(),  # 使用 set 便于去重
                        "online": set(),
                        "created_at": time.time()
                    }
                    # 公共房间加入展示
                    if domain_enum == 1:  # 公共房间
                        ROOM_PUBLIC.append(room_code)
                    # 创建者自动加入房间
                    join_room(user_id, room_code)
                    
                    # 记录业务事件
                    log_business_event("create_room", user_id, room_code, {"domain": room_domain})
                    
                    return room_code
        
        # 重试次数用尽
        error_msg = f"无法生成唯一房间码，已重试 {max_retries} 次"
        logger.error(error_msg)
        raise RoomServiceError(error_msg, "ROOM_CODE_GENERATION_FAILED")
        
    except Exception as e:
        log_exception(e, "创建房间失败", {"user_id": user_id, "domain_enum": domain_enum})
        if isinstance(e, RoomServiceError):
            raise
        raise RoomServiceError(f"创建房间失败: {str(e)}", "CREATE_ROOM_FAILED")


def join_room(user_id: str, room_code: str):
    """
    加入房间
    
    Args:
        user_id: 用户ID
        room_code: 房间码
    
    Raises:
        RoomNotFoundError: 房间不存在时抛出
    """
    try:
        room = get_room_info(room_code)
        room["members"].add(user_id)
        room["online"].add(user_id)
        
        # 记录业务事件
        log_business_event("join_room", user_id, room_code)
        
    except RoomNotFoundError:
        raise
    except Exception as e:
        log_exception(e, "加入房间失败", {"user_id": user_id, "room_code": room_code})
        raise RoomServiceError(f"加入房间失败: {str(e)}", "JOIN_ROOM_FAILED")


def leave_room(user_id: str, room_code: str):
    """
    离开房间
    
    Args:
        user_id: 用户ID
        room_code: 房间码
    
    Raises:
        RoomNotFoundError: 房间不存在时抛出
        UserNotInRoomError: 用户不在房间时抛出
    """
    try:
        room = get_room_info(room_code)
        
        if user_id not in room["members"]:
            raise UserNotInRoomError(user_id, room_code)
        
        try:
            room["online"].remove(user_id)
        except KeyError:
            # 用户不在在线列表中，这是正常的
            pass
        
        # 记录业务事件
        log_business_event("leave_room", user_id, room_code)
        
    except (RoomNotFoundError, UserNotInRoomError):
        raise
    except Exception as e:
        log_exception(e, "离开房间失败", {"user_id": user_id, "room_code": room_code})
        raise RoomServiceError(f"离开房间失败: {str(e)}", "LEAVE_ROOM_FAILED")


def get_public_room_list() -> list:
    """
    获取公共房间列表
    
    Returns:
        公共房间信息列表
    """
    try:
        rooms = []
        for room_code in ROOM_PUBLIC:
            if room_code in ROOM_MANAGER:
                room_info = ROOM_MANAGER[room_code].copy()
                room_info["room_code"] = room_code
                rooms.append(room_info)
        
        # 记录业务事件
        log_business_event("get_public_rooms", details={"count": len(rooms)})
        
        return rooms
    except Exception as e:
        log_exception(e, "获取公共房间列表失败")
        raise RoomServiceError(f"获取公共房间列表失败: {str(e)}", "GET_PUBLIC_ROOMS_FAILED")