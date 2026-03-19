import random
import string
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

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
ROOM_MANAGER: Dict[str, Dict[str, Any]] = {}
# 公共房间
ROOM_PUBLIC: List[str] = []

# 线程锁
room_lock = threading.Lock()


def generate_room_code(length: int = 5) -> str:
    """生成包含大写字母和数字的随机字符串"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length))


def get_room_info(room_code: str) -> dict:
    """获取房间信息
    
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
    """获取房间领域枚举值
    
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
    """创建房间（线程安全）

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
                        "created_at": time.time(),
                        "messages": []  # 房间消息历史
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
    """加入房间
    
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
    """离开房间
    
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
    """获取公共房间列表
    
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


def add_room_message(room_code: str, username: str, message: str) -> dict:
    """添加房间消息到历史记录
    
    Args:
        room_code: 房间码
        username: 用户名
        message: 消息内容
    
    Returns:
        添加结果
    """
    try:
        room = get_room_info(room_code)

        # 检查用户是否在房间中
        if username not in room["members"]:
            raise UserNotInRoomError(username, room_code)

        # 添加消息到历史记录
        message_record = {
            "username": username,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "timestamp_unix": time.time()
        }

        room["messages"].append(message_record)

        # 限制消息历史数量（最多保留100条）
        if len(room["messages"]) > 100:
            room["messages"] = room["messages"][-100:]

        # 记录业务事件
        log_business_event("send_room_message", username, room_code, {
            "message_length": len(message),
            "message_count": len(room["messages"])
        })

        return {
            "success": True,
            "message": "消息已添加到房间历史",
            "message_record": message_record
        }

    except (RoomNotFoundError, UserNotInRoomError) as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        log_exception(e, "添加房间消息失败", {"room_code": room_code, "username": username})
        return {"success": False, "message": f"添加消息失败: {str(e)}"}


def get_room_messages(room_code: str, limit: int = 50) -> dict:
    """获取房间消息历史
    
    Args:
        room_code: 房间码
        limit: 限制数量
    
    Returns:
        消息历史列表
    """
    try:
        room = get_room_info(room_code)

        # 获取最新的消息
        messages = room["messages"][-limit:] if limit > 0 else room["messages"]

        return {
            "success": True,
            "message": "获取消息历史成功",
            "messages": messages,
            "total": len(messages),
            "room_code": room_code
        }

    except RoomNotFoundError as e:
        return {"success": False, "message": str(e), "messages": [], "total": 0}
    except Exception as e:
        log_exception(e, "获取房间消息失败", {"room_code": room_code})
        return {"success": False, "message": f"获取消息失败: {str(e)}", "messages": [], "total": 0}


def get_room_online_users(room_code: str) -> dict:
    """获取房间在线用户列表
    
    Args:
        room_code: 房间码
    
    Returns:
        在线用户列表
    """
    try:
        room = get_room_info(room_code)
        online_users = list(room["online"])

        return {
            "success": True,
            "message": "获取在线用户列表成功",
            "online_users": online_users,
            "total": len(online_users),
            "room_code": room_code
        }

    except RoomNotFoundError as e:
        return {"success": False, "message": str(e), "online_users": [], "total": 0}
    except Exception as e:
        log_exception(e, "获取房间在线用户失败", {"room_code": room_code})
        return {"success": False, "message": f"获取在线用户失败: {str(e)}", "online_users": [], "total": 0}


def get_room_members(room_code: str) -> dict:
    """获取房间成员列表
    
    Args:
        room_code: 房间码
    
    Returns:
        成员列表
    """
    try:
        room = get_room_info(room_code)
        members = list(room["members"])

        return {
            "success": True,
            "message": "获取成员列表成功",
            "members": members,
            "total": len(members),
            "room_code": room_code
        }

    except RoomNotFoundError as e:
        return {"success": False, "message": str(e), "members": [], "total": 0}
    except Exception as e:
        log_exception(e, "获取房间成员失败", {"room_code": room_code})
        return {"success": False, "message": f"获取成员失败: {str(e)}", "members": [], "total": 0}


def broadcast_to_room(room_code: str, message_data: Dict[str, Any],
                      exclude_user: Optional[str] = None) -> dict:
    """向房间内所有用户广播消息
    
    Args:
        room_code: 房间码
        message_data: 消息数据
        exclude_user: 要排除的用户（可选）
    
    Returns:
        广播结果
    """
    try:
        room = get_room_info(room_code)
        online_users = room["online"]

        # 记录广播事件
        log_business_event("broadcast_to_room", details={
            "room_code": room_code,
            "online_count": len(online_users),
            "exclude_user": exclude_user,
            "message_type": message_data.get("type", "unknown")
        })

        return {
            "success": True,
            "message": "广播消息已发送",
            "room_code": room_code,
            "online_users": list(online_users),
            "exclude_user": exclude_user,
            "total_recipients": len(online_users) - (1 if exclude_user and exclude_user in online_users else 0)
        }

    except RoomNotFoundError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        log_exception(e, "房间广播失败", {"room_code": room_code})
        return {"success": False, "message": f"广播失败: {str(e)}"}


def cleanup_inactive_rooms(max_inactive_time: int = 3600) -> dict:
    """清理不活跃的房间
    
    Args:
        max_inactive_time: 最大不活跃时间（秒）
    
    Returns:
        清理结果
    """
    try:
        current_time = time.time()
        removed_rooms = []

        with room_lock:
            room_codes_to_remove = []

            for room_code, room_info in ROOM_MANAGER.items():
                # 检查房间是否长时间没有活动
                last_activity = room_info.get("last_activity", room_info.get("created_at", 0))

                # 如果房间没有成员或者长时间没有活动，则清理
                if (len(room_info["members"]) == 0 or
                        current_time - last_activity > max_inactive_time):
                    room_codes_to_remove.append(room_code)

            # 移除不活跃房间
            for room_code in room_codes_to_remove:
                if room_code in ROOM_MANAGER:
                    del ROOM_MANAGER[room_code]

                    # 从公共房间列表中移除
                    if room_code in ROOM_PUBLIC:
                        ROOM_PUBLIC.remove(room_code)

                    removed_rooms.append(room_code)
                    logger.info(f"清理不活跃房间: {room_code}")

        return {
            "success": True,
            "message": f"清理完成，移除了 {len(removed_rooms)} 个不活跃房间",
            "removed_rooms": removed_rooms,
            "total_removed": len(removed_rooms)
        }

    except Exception as e:
        log_exception(e, "清理不活跃房间失败")
        return {"success": False, "message": f"清理失败: {str(e)}", "removed_rooms": [], "total_removed": 0}
