"""用户服务层（业务逻辑层）"""

import threading
import time
from typing import Dict, List, Optional, Any

from log.logger import logger, log_exception, log_business_event
from dao.user import UserDao, User  # 添加User模型导入
from security.passwords import hash_password, is_hashed, verify_password

# 用户数据结构
USERS_ONLINE: Dict[str, Dict[str, Any]] = {}
# 线程锁
user_lock = threading.Lock()


class UserService:
    """用户服务类"""

    @staticmethod
    async def create_user(
        username: str,
        password: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建用户
        
        Args:
            username: 用户名
            password: 密码（可选）
            
        Returns:
            创建结果
        """
        try:
            # 口令只以哈希形式写库；password 为空（None/""）时保持 None。
            stored_password = hash_password(password) if password else None
            # 调用数据访问层创建用户
            user = await UserDao.create_user(
                username, stored_password, nickname=nickname
            )
            
            # 记录业务事件
            log_business_event("user_created", username, details={"action": "创建用户"})
            
            return {
                "success": True,
                "message": "用户创建成功",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "nickname": getattr(user, "nickname", None) or user.username,
                }
            }
                
        except Exception as e:
            log_exception(e, "创建用户失败", {"username": username})
            return {
                "success": False,
                "message": "创建用户失败"
            }

    @staticmethod
    async def get_user_by_id(user_id: int) -> Dict[str, Any]:
        """根据ID获取用户信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            用户信息
        """
        try:
            user = await UserDao.get_user_by_id(user_id)
            
            if user:
                return {
                    "success": True,
                    "message": "获取用户信息成功",
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "nickname": getattr(user, "nickname", None) or user.username,
                        "created_at": user.created_at,
                        "updated_at": user.updated_at
                    }
                }
            else:
                return {
                    "success": False,
                    "message": "用户不存在"
                }
                
        except Exception as e:
            log_exception(e, "获取用户信息失败", {"user_id": user_id})
            return {
                "success": False,
                "message": "获取用户信息失败"
            }

    @staticmethod
    async def get_user_by_username(username: str) -> Dict[str, Any]:
        """根据用户名获取用户信息
        
        Args:
            username: 用户名
            
        Returns:
            用户信息
        """
        try:
            user = await UserDao.get_user_by_username(username)
            
            if user:
                return {
                    "success": True,
                    "message": "获取用户信息成功",
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "nickname": getattr(user, "nickname", None) or user.username,
                        "created_at": user.created_at,
                        "updated_at": user.updated_at
                    }
                }
            else:
                return {
                    "success": False,
                    "message": "用户不存在"
                }
                
        except Exception as e:
            log_exception(e, "获取用户信息失败", {"username": username})
            return {
                "success": False,
                "message": "获取用户信息失败"
            }

    @staticmethod
    async def update_user(user_id: int, **kwargs) -> Dict[str, Any]:
        """更新用户信息
        
        Args:
            user_id: 用户ID
            **kwargs: 更新字段
            
        Returns:
            更新结果
        """
        try:
            # 口令字段必须先哈希，禁止把明文写库（更新其它字段时不受影响）。
            if kwargs.get("password"):
                kwargs = dict(kwargs)
                kwargs["password"] = hash_password(kwargs["password"])
            user = await UserDao.update_user(user_id, **kwargs)
            
            if user:
                # 记录业务事件
                log_business_event("user_updated", user.username, details={"action": "更新用户信息", "fields": list(kwargs.keys())})
                
                return {
                    "success": True,
                    "message": "用户信息更新成功",
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "nickname": getattr(user, "nickname", None) or user.username,
                    }
                }
            else:
                return {
                    "success": False,
                    "message": "用户不存在或更新失败"
                }
                
        except Exception as e:
            log_exception(e, "更新用户信息失败", {"user_id": user_id})
            return {
                "success": False,
                "message": "更新用户信息失败"
            }

    @staticmethod
    async def update_nickname(user_id: int, nickname: str) -> Dict[str, Any]:
        """修改昵称（不要求唯一）。"""
        user = await UserDao.update_user(user_id, nickname=nickname)
        if not user:
            return {"success": False, "message": "用户不存在"}
        return {
            "success": True,
            "message": "昵称更新成功",
            "user": {
                "id": user.id,
                "username": user.username,
                "nickname": user.nickname,
            },
        }

    @staticmethod
    async def change_password(
        user_id: int, old_password: str, new_password: str
    ) -> Dict[str, Any]:
        """修改密码；旧密码错误不修改，成功不吊销已签发 token。"""
        user = await UserDao.get_user_by_id(user_id)
        if user is None:
            return {"success": False, "message": "用户不存在"}
        if not verify_password(old_password, user.password):
            return {
                "success": False,
                "error_code": "WRONG_OLD_PASSWORD",
                "message": "旧密码错误",
            }
        await UserDao.update_user(user_id, password=hash_password(new_password))
        return {"success": True, "message": "密码修改成功"}

    @staticmethod
    async def delete_user(user_id: int) -> Dict[str, Any]:
        """删除用户
        
        Args:
            user_id: 用户ID
            
        Returns:
            删除结果
        """
        try:
            success = await UserDao.delete_user(user_id)
            
            if success:
                # 记录业务事件
                log_business_event("user_deleted", details={"action": "删除用户", "user_id": user_id})
                
                return {
                    "success": True,
                    "message": "用户删除成功"
                }
            else:
                return {
                    "success": False,
                    "message": "用户不存在或删除失败"
                }
                
        except Exception as e:
            log_exception(e, "删除用户失败", {"user_id": user_id})
            return {
                "success": False,
                "message": "删除用户失败"
            }

    @staticmethod
    async def list_users(skip: int = 0, limit: int = 100) -> Dict[str, Any]:
        """获取用户列表
        
        Args:
            skip: 跳过数量
            limit: 限制数量
            
        Returns:
            用户列表
        """
        try:
            users = await UserDao.list_users(skip, limit)
            
            # 转换为字典列表
            user_list = [
                {
                    "id": user.id,
                    "username": user.username,
                    "nickname": getattr(user, "nickname", None) or user.username,
                    "created_at": user.created_at,
                    "updated_at": user.updated_at
                }
                for user in users
            ]
            
            # 获取总用户数（需要额外查询）
            total_count = await User.filter(deleted=False).count()
            
            # 记录业务事件
            log_business_event("list_users", details={"action": "获取用户列表", "count": len(user_list)})
            
            return {
                "success": True,
                "message": "获取用户列表成功",
                "users": user_list,
                "total": total_count
            }
                
        except Exception as e:
            log_exception(e, "获取用户列表失败")
            return {
                "success": False,
                "message": "获取用户列表失败",
                "users": [],
                "total": 0
            }

    @staticmethod
    async def login(username: str, password: str) -> Dict[str, Any]:
        """用户登录
        
        Args:
            username: 用户名
            password: 密码
            
        Returns:
            登录结果
        """
        try:
            # 按用户名取用户后再校验口令：不再把明文口令拼进 SQL 等值条件。
            user = await UserDao.get_user_by_username(username)

            if user is not None and verify_password(password, user.password):
                # 兼容历史明文口令：登录成功即重哈希写回，后续不再保留明文。
                if not is_hashed(user.password):
                    try:
                        await UserDao.update_user(
                            user.id, password=hash_password(password)
                        )
                    except Exception as rehash_error:
                        # 重哈希失败不阻断登录，但要留服务端日志；迁移脚本可兜底。
                        log_exception(
                            rehash_error,
                            "登录成功但口令重哈希失败",
                            {"username": username},
                        )

                # 登录成功，更新用户在线状态
                await UserService.update_user_activity(username)
                
                # 记录业务事件
                log_business_event("user_login", username, details={"action": "登录成功"})
                
                return {
                    "success": True,
                    "message": "登录成功",
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "nickname": getattr(user, "nickname", None) or user.username,
                    }
                }
            else:
                # 记录登录失败事件
                log_business_event("user_login_failed", username, details={"action": "登录失败", "reason": "用户名或密码错误"})
                
                return {
                    "success": False,
                    "message": "用户名或密码错误"
                }
                
        except Exception as e:
            log_exception(e, "用户登录失败", {"username": username})
            return {
                "success": False,
                "message": "登录失败"
            }

    @staticmethod
    async def update_user_activity(username: str) -> bool:
        """更新用户活动时间
        
        Args:
            username: 用户名
        
        Returns:
            更新是否成功
        """
        try:
            with user_lock:
                if username not in USERS_ONLINE:
                    USERS_ONLINE[username] = {
                        "username": username,
                        "last_activity": time.time(),
                        "login_time": time.time(),
                        "websocket": None,
                        "status": "online"
                    }
                else:
                    USERS_ONLINE[username]["last_activity"] = time.time()

            # 记录业务事件
            log_business_event("user_activity", username, details={"action": "更新活动时间"})

            return True

        except Exception as e:
            log_exception(e, "更新用户活动时间失败", {"username": username})
            return False

    @staticmethod
    async def update_user_websocket(username: str, websocket: Optional[Any]) -> bool:
        """更新用户WebSocket连接信息
        
        Args:
            username: 用户名
            websocket: WebSocket连接对象，None表示断开连接
        
        Returns:
            更新是否成功
        """
        try:
            with user_lock:
                if username in USERS_ONLINE:
                    USERS_ONLINE[username]["websocket"] = websocket

                    # 如果WebSocket为None，表示用户离线
                    if websocket is None:
                        USERS_ONLINE[username]["status"] = "offline"
                        USERS_ONLINE[username]["logout_time"] = time.time()
                    else:
                        USERS_ONLINE[username]["status"] = "online"
                else:
                    # 用户不在线，创建新的在线记录
                    USERS_ONLINE[username] = {
                        "username": username,
                        "last_activity": time.time(),
                        "login_time": time.time(),
                        "websocket": websocket,
                        "status": "online"
                    }

            action = "断开连接" if websocket is None else "连接建立"
            log_business_event("websocket_update", username, details={
                "action": action,
                "websocket_id": str(id(websocket))[-6:] if websocket else None
            })

            return True

        except Exception as e:
            log_exception(e, "更新用户WebSocket信息失败", {"username": username})
            return False

    @staticmethod
    async def check_user_online(username: str) -> Dict[str, Any]:
        """检查用户是否在线
        
        Args:
            username: 用户名
        
        Returns:
            检查结果
        """
        try:
            with user_lock:
                if username in USERS_ONLINE:
                    user_info = USERS_ONLINE[username]

                    # 检查用户是否超时（5分钟无活动视为离线）
                    current_time = time.time()
                    last_activity = user_info.get("last_activity", 0)

                    if current_time - last_activity > 300:  # 5分钟超时
                        # 用户超时，标记为离线
                        user_info["status"] = "offline"
                        user_info["logout_time"] = current_time

                        return {
                            "online": False,
                            "message": "用户已超时离线",
                            "last_activity": last_activity,
                            "timeout": True
                        }

                    return {
                        "online": True,
                        "username": username,
                        "last_activity": last_activity,
                        "login_time": user_info.get("login_time"),
                        "status": user_info.get("status", "online"),
                        "websocket": user_info.get("websocket") is not None
                    }
                else:
                    return {
                        "online": False,
                        "message": "用户不在线",
                        "username": username
                    }

        except Exception as e:
            log_exception(e, "检查用户在线状态失败", {"username": username})
            return {
                "online": False,
                "message": "检查失败",
                "username": username
            }

    @staticmethod
    async def get_online_users() -> List[Dict[str, Any]]:
        """获取所有在线用户列表
        
        Returns:
            在线用户列表
        """
        try:
            with user_lock:
                online_users = []
                current_time = time.time()

                for username, user_info in USERS_ONLINE.items():
                    # 检查用户是否真正在线（3分钟内活动）
                    last_activity = user_info.get("last_activity", 0)

                    if current_time - last_activity <= 180:  # 3分钟内有活动
                        online_users.append({
                            "username": username,
                            "last_activity": last_activity,
                            "login_time": user_info.get("login_time"),
                            "status": user_info.get("status", "online"),
                            "websocket": user_info.get("websocket") is not None,
                            "inactive_minutes": round((current_time - last_activity) / 60, 1)
                        })

                # 按最后活动时间排序（最近活动的在前）
                online_users.sort(key=lambda x: x["last_activity"], reverse=True)

                # 记录业务事件
                log_business_event("get_online_users", details={
                    "count": len(online_users),
                    "total_users": len(USERS_ONLINE)
                })

                return online_users

        except Exception as e:
            log_exception(e, "获取在线用户列表失败")
            return []

    @staticmethod
    async def check_and_remove_inactive_users(inactive_threshold: int = 300) -> Dict[str, Any]:
        """检查并移除不活跃用户
        
        Args:
            inactive_threshold: 不活跃阈值（秒），默认5分钟
        
        Returns:
            移除结果
        """
        try:
            removed_users = []
            current_time = time.time()

            with user_lock:
                users_to_remove = []

                for username, user_info in USERS_ONLINE.items():
                    last_activity = user_info.get("last_activity", 0)

                    # 如果用户超过阈值时间无活动，则移除
                    if current_time - last_activity > inactive_threshold:
                        users_to_remove.append(username)

                # 移除不活跃用户
                for username in users_to_remove:
                    user_info = USERS_ONLINE.pop(username, None)
                    if user_info:
                        removed_users.append(username)

                        # 记录用户离线事件
                        log_business_event("user_timeout", username, details={
                            "action": "超时移除",
                            "last_activity": user_info.get("last_activity"),
                            "inactive_minutes": round((current_time - last_activity) / 60, 1)
                        })

            result = {
                "success": True,
                "message": f"检查完成，移除了 {len(removed_users)} 个不活跃用户",
                "removed_users": removed_users,
                "total_removed": len(removed_users),
                "threshold_seconds": inactive_threshold,
                "threshold_minutes": round(inactive_threshold / 60, 1)
            }

            if removed_users:
                logger.info(f"离线检测: 移除了 {len(removed_users)} 个不活跃用户: {removed_users}")

            return result

        except Exception as e:
            log_exception(e, "检查不活跃用户失败")
            return {
                "success": False,
                "message": "检查失败",
                "removed_users": [],
                "total_removed": 0
            }

    @staticmethod
    async def bind_websocket_connection(username: str, websocket: Any) -> bool:
        """绑定WebSocket连接到用户
        
        Args:
            username: 用户名
            websocket: WebSocket连接对象
        
        Returns:
            绑定是否成功
        """
        try:
            with user_lock:
                if username not in USERS_ONLINE:
                    # 创建新的在线用户记录
                    USERS_ONLINE[username] = {
                        "username": username,
                        "last_activity": time.time(),
                        "login_time": time.time(),
                        "websocket": websocket,
                        "status": "online"
                    }
                else:
                    # 更新现有用户的WebSocket连接
                    USERS_ONLINE[username]["websocket"] = websocket
                    USERS_ONLINE[username]["status"] = "online"
                    USERS_ONLINE[username]["last_activity"] = time.time()

            # 记录业务事件
            log_business_event("websocket_bind", username, details={
                "action": "绑定WebSocket连接",
                "websocket_id": str(id(websocket))[-6:]
            })

            return True

        except Exception as e:
            log_exception(e, "绑定WebSocket连接失败", {"username": username})
            return False

    @staticmethod
    async def unbind_websocket_connection(username: str) -> bool:
        """解绑用户的WebSocket连接
        
        Args:
            username: 用户名
        
        Returns:
            解绑是否成功
        """
        try:
            with user_lock:
                if username in USERS_ONLINE:
                    USERS_ONLINE[username]["websocket"] = None
                    USERS_ONLINE[username]["status"] = "offline"
                    USERS_ONLINE[username]["logout_time"] = time.time()

            # 记录业务事件
            log_business_event("websocket_unbind", username, details={"action": "解绑WebSocket连接"})

            return True

        except Exception as e:
            log_exception(e, "解绑WebSocket连接失败", {"username": username})
            return False

    @staticmethod
    async def get_user_websocket(username: str) -> Optional[Any]:
        """获取用户的WebSocket连接
        
        Args:
            username: 用户名
        
        Returns:
            WebSocket连接对象，如果用户不在线或未连接则返回None
        """
        try:
            with user_lock:
                if username in USERS_ONLINE:
                    return USERS_ONLINE[username].get("websocket")
                return None

        except Exception as e:
            log_exception(e, "获取用户WebSocket连接失败", {"username": username})
            return None

    @staticmethod
    async def force_logout_user(username: str) -> bool:
        """强制用户下线
        
        Args:
            username: 用户名
        
        Returns:
            强制下线是否成功
        """
        try:
            with user_lock:
                if username in USERS_ONLINE:
                    # 强制关闭WebSocket连接（如果存在）
                    websocket = USERS_ONLINE[username].get("websocket")
                    if websocket:
                        try:
                            await websocket.close()
                        except Exception:
                            pass

                    # 移除用户在线记录
                    USERS_ONLINE.pop(username, None)

                    # 记录业务事件
                    log_business_event("force_logout", username, details={"action": "强制下线"})

                    return True
                else:
                    return False

        except Exception as e:
            log_exception(e, "强制用户下线失败", {"username": username})
            return False