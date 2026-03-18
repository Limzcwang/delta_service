from typing import Optional, List
from dao.user import UserDao, User_Pydantic, UserIn_Pydantic
import hashlib
import logging

logger = logging.getLogger(__name__)


class UserService:
    """用户服务层"""

    @staticmethod
    def _hash_password(password: str) -> str:
        """简单的密码哈希处理
        
        Args:
            password: 明文密码
            
        Returns:
            哈希后的密码
        """
        return hashlib.md5(password.encode()).hexdigest()

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