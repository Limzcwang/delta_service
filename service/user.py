from typing import Optional, List, Dict, Any
from datetime import datetime
from dao.user import UserDao, User_Pydantic, UserIn_Pydantic
import hashlib
import logging

logger = logging.getLogger(__name__)

# 在线用户数据结构
USERS_ONLINE: List[Dict[str, Any]] = []

# 用户最后活动时间跟踪
USER_LAST_ACTIVITY: Dict[str, datetime] = {}

# 离线检测间隔（秒）
OFFLINE_CHECK_INTERVAL = 60

# 最大无活动时间（秒）
MAX_INACTIVITY_TIME = 300  # 5分钟

class UserService:
    """用户服务层"""

    @staticmethod
    async def create_user(username: str, password: Optional[str] = None) -> dict:
        """创建用户
        
        Args:
            username: 用户名
            password: 密码（可选）
            
        Returns:
            创建的用户信息
        """
        try:
            # 检查用户名是否已存在
            existing_user = await UserDao.get_user_by_username(username)
            if existing_user:
                return {"success": False, "message": "用户名已存在", "user": None}

            # 创建用户
            user = await UserDao.create_user(username, password)
            user_data = await User_Pydantic.from_tortoise_orm(user)

            logger.info(f"用户创建成功: {username}")
            return {"success": True, "message": "用户创建成功", "user": user_data.dict()}

        except Exception as e:
            logger.error(f"创建用户失败: {str(e)}")
            return {"success": False, "message": f"创建用户失败: {str(e)}", "user": None}

    @staticmethod
    async def get_user_by_id(user_id: int) -> dict:
        """根据ID获取用户信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            用户信息
        """
        try:
            user = await UserDao.get_user_by_id(user_id)
            if not user:
                return {"success": False, "message": "用户不存在", "user": None}

            user_data = await User_Pydantic.from_tortoise_orm(user)
            return {"success": True, "message": "获取用户成功", "user": user_data.dict()}

        except Exception as e:
            logger.error(f"获取用户失败: {str(e)}")
            return {"success": False, "message": f"获取用户失败: {str(e)}", "user": None}

    @staticmethod
    async def get_user_by_username(username: str) -> dict:
        """根据用户名获取用户信息
        
        Args:
            username: 用户名
            
        Returns:
            用户信息
        """
        try:
            user = await UserDao.get_user_by_username(username)
            if not user:
                return {"success": False, "message": "用户不存在", "user": None}

            user_data = await User_Pydantic.from_tortoise_orm(user)
            return {"success": True, "message": "获取用户成功", "user": user_data.dict()}

        except Exception as e:
            logger.error(f"获取用户失败: {str(e)}")
            return {"success": False, "message": f"获取用户失败: {str(e)}", "user": None}

    @staticmethod
    async def update_user(user_id: int, **kwargs) -> dict:
        """更新用户信息
        
        Args:
            user_id: 用户ID
            **kwargs: 要更新的字段
            
        Returns:
            更新结果
        """
        try:
            # 检查用户是否存在
            existing_user = await UserDao.get_user_by_id(user_id)
            if not existing_user:
                return {"success": False, "message": "用户不存在"}

            # 更新用户信息
            updated_user = await UserDao.update_user(user_id, **kwargs)
            if updated_user:
                user_data = await User_Pydantic.from_tortoise_orm(updated_user)
                logger.info(f"用户信息更新成功: {user_id}")
                return {"success": True, "message": "用户信息更新成功", "user": user_data.dict()}
            else:
                return {"success": False, "message": "用户信息更新失败"}

        except Exception as e:
            logger.error(f"更新用户信息失败: {str(e)}")
            return {"success": False, "message": f"更新用户信息失败: {str(e)}"}

    @staticmethod
    async def delete_user(user_id: int) -> dict:
        """删除用户（软删除）
        
        Args:
            user_id: 用户ID
            
        Returns:
            删除结果
        """
        try:
            # 检查用户是否存在
            existing_user = await UserDao.get_user_by_id(user_id)
            if not existing_user:
                return {"success": False, "message": "用户不存在"}

            # 执行软删除
            success = await UserDao.delete_user(user_id)
            if success:
                logger.info(f"用户删除成功: {user_id}")
                return {"success": True, "message": "用户删除成功"}
            else:
                return {"success": False, "message": "用户删除失败"}

        except Exception as e:
            logger.error(f"删除用户失败: {str(e)}")
            return {"success": False, "message": f"删除用户失败: {str(e)}"}

    @staticmethod
    async def login(username: str, password: str) -> dict:
        """用户登录
        
        Args:
            username: 用户名
            password: 密码
            
        Returns:
            登录结果
        """
        try:
            # 验证登录
            user = await UserDao.login(username, password)
            if user:
                user_data = await User_Pydantic.from_tortoise_orm(user)
                logger.info(f"用户登录成功: {username}")
                
                # 更新最后活动时间
                USER_LAST_ACTIVITY[username] = datetime.now()
                
                # 检查用户是否已在线，如果在线则更新信息
                user_found = False
                for i, online_user in enumerate(USERS_ONLINE):
                    if online_user.get("username") == username:
                        USERS_ONLINE[i] = {
                            "username": username,
                            "user_data": user_data.dict(),
                            "last_activity": datetime.now(),
                            "websocket": online_user.get("websocket"),
                            "login_time": datetime.now()
                        }
                        user_found = True
                        break
                
                # 如果用户不在线，添加到在线列表
                if not user_found:
                    USERS_ONLINE.append({
                        "username": username,
                        "user_data": user_data.dict(),
                        "last_activity": datetime.now(),
                        "websocket": None,
                        "login_time": datetime.now()
                    })
                    logger.info(f"用户添加到在线列表: {username}")
                
                return {"success": True, "message": "登录成功", "user": user_data.dict()}
            else:
                return {"success": False, "message": "用户名或密码错误", "user": None}

        except Exception as e:
            logger.error(f"用户登录失败: {str(e)}")
            return {"success": False, "message": f"登录失败: {str(e)}", "user": None}

    @staticmethod
    async def list_users(skip: int = 0, limit: int = 100) -> dict:
        """获取用户列表
        
        Args:
            skip: 跳过数量
            limit: 限制数量
            
        Returns:
            用户列表
        """
        try:
            # 获取未删除的用户列表
            users = await UserDao.list_users(skip, limit)
            users_data = [await User_Pydantic.from_tortoise_orm(user) for user in users]

            return {
                "success": True,
                "message": "获取用户列表成功",
                "users": [user.dict() for user in users_data],
                "total": len(users_data)
            }

        except Exception as e:
            logger.error(f"获取用户列表失败: {str(e)}")
            return {"success": False, "message": f"获取用户列表失败: {str(e)}", "users": [], "total": 0}

    @staticmethod
    async def logout(username: str) -> dict:
        """用户登出
        
        Args:
            username: 用户名
            
        Returns:
            登出结果
        """
        try:
            # 从在线列表中移除用户
            for i, online_user in enumerate(USERS_ONLINE):
                if online_user.get("username") == username:
                    USERS_ONLINE.pop(i)
                    # 清理最后活动时间记录
                    if username in USER_LAST_ACTIVITY:
                        del USER_LAST_ACTIVITY[username]
                    logger.info(f"用户登出成功，已从在线列表移除: {username}")
                    return {"success": True, "message": "登出成功"}
            
            logger.info(f"用户不在在线列表中: {username}")
            return {"success": False, "message": "用户不在线"}
            
        except Exception as e:
            logger.error(f"用户登出失败: {str(e)}")
            return {"success": False, "message": f"登出失败: {str(e)}"}

    @staticmethod
    async def check_user_online(username: str) -> dict:
        """检查用户是否在线
        
        Args:
            username: 用户名
            
        Returns:
            在线状态
        """
        try:
            for online_user in USERS_ONLINE:
                if online_user.get("username") == username:
                    return {
                        "success": True, 
                        "message": "用户在线", 
                        "online": True, 
                        "user": online_user.get("user_data"),
                        "last_activity": online_user.get("last_activity"),
                        "login_time": online_user.get("login_time")
                    }
            
            return {"success": True, "message": "用户离线", "online": False, "user": None}
            
        except Exception as e:
            logger.error(f"检查用户在线状态失败: {str(e)}")
            return {"success": False, "message": f"检查在线状态失败: {str(e)}", "online": False, "user": None}

    @staticmethod
    async def remove_offline_user(username: str) -> dict:
        """移除离线用户
        
        Args:
            username: 用户名
            
        Returns:
            移除结果
        """
        try:
            # 检查用户是否在数据库中
            user = await UserDao.get_user_by_username(username)
            if not user:
                # 如果用户不存在于数据库，从在线列表移除
                for i, online_user in enumerate(USERS_ONLINE):
                    if online_user.get("username") == username:
                        USERS_ONLINE.pop(i)
                        if username in USER_LAST_ACTIVITY:
                            del USER_LAST_ACTIVITY[username]
                        logger.info(f"移除不存在的用户: {username}")
                        return {"success": True, "message": "用户不存在，已从在线列表移除"}
                return {"success": False, "message": "用户不存在且不在在线列表"}
            
            # 如果用户存在但需要强制移除（比如检测到离线）
            for i, online_user in enumerate(USERS_ONLINE):
                if online_user.get("username") == username:
                    USERS_ONLINE.pop(i)
                    if username in USER_LAST_ACTIVITY:
                        del USER_LAST_ACTIVITY[username]
                    logger.info(f"用户离线，已从在线列表移除: {username}")
                    return {"success": True, "message": "用户已离线，已从在线列表移除"}
            
            logger.info(f"用户不在在线列表中: {username}")
            return {"success": False, "message": "用户不在在线列表"}
            
        except Exception as e:
            logger.error(f"移除离线用户失败: {str(e)}")
            return {"success": False, "message": f"移除离线用户失败: {str(e)}"}

    @staticmethod
    async def get_online_users() -> dict:
        """获取所有在线用户列表
        
        Returns:
            在线用户列表
        """
        try:
            online_users = []
            for online_user in USERS_ONLINE:
                online_users.append({
                    "username": online_user.get("username"),
                    "user_data": online_user.get("user_data"),
                    "last_activity": online_user.get("last_activity"),
                    "login_time": online_user.get("login_time"),
                    "has_websocket": online_user.get("websocket") is not None
                })
            
            return {
                "success": True, 
                "message": "获取在线用户列表成功", 
                "online_users": online_users,
                "total": len(online_users)
            }
            
        except Exception as e:
            logger.error(f"获取在线用户列表失败: {str(e)}")
            return {"success": False, "message": f"获取在线用户列表失败: {str(e)}", "online_users": [], "total": 0}

    @staticmethod
    async def update_user_activity(username: str) -> dict:
        """更新用户活动时间
        
        Args:
            username: 用户名
            
        Returns:
            更新结果
        """
        try:
            current_time = datetime.now()
            USER_LAST_ACTIVITY[username] = current_time
            
            # 同时更新在线用户列表中的最后活动时间
            for online_user in USERS_ONLINE:
                if online_user.get("username") == username:
                    online_user["last_activity"] = current_time
                    break
            
            return {"success": True, "message": "用户活动时间更新成功"}
            
        except Exception as e:
            logger.error(f"更新用户活动时间失败: {str(e)}")
            return {"success": False, "message": f"更新用户活动时间失败: {str(e)}"}

    @staticmethod
    async def check_and_remove_inactive_users() -> dict:
        """检查并移除不活跃用户
        
        Returns:
            移除结果
        """
        try:
            current_time = datetime.now()
            removed_users = []
            
            for i in range(len(USERS_ONLINE) - 1, -1, -1):
                online_user = USERS_ONLINE[i]
                username = online_user.get("username")
                last_activity = online_user.get("last_activity")
                
                if last_activity and (current_time - last_activity).total_seconds() > MAX_INACTIVITY_TIME:
                    # 用户超时，移除
                    USERS_ONLINE.pop(i)
                    if username in USER_LAST_ACTIVITY:
                        del USER_LAST_ACTIVITY[username]
                    removed_users.append(username)
                    logger.info(f"自动移除不活跃用户: {username}")
            
            return {
                "success": True, 
                "message": f"检查完成，移除了 {len(removed_users)} 个不活跃用户",
                "removed_users": removed_users
            }
            
        except Exception as e:
            logger.error(f"检查不活跃用户失败: {str(e)}")
            return {"success": False, "message": f"检查不活跃用户失败: {str(e)}", "removed_users": []}