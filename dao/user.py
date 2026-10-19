from typing import Optional

from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator


class User(models.Model):
    """用户模型"""
    id = fields.IntField(pk=True)
    username = fields.CharField(max_length=50, unique=True, description="用户名（仅本人可见）")
    # 对外展示名；迁移脚本为老账号回填为 username，新注册默认等于 username。
    nickname = fields.CharField(max_length=32, default="", description="对外昵称")
    # 存 scrypt 哈希（scrypt$N$r$p$salt$hash），不再存明文；255 为参数升级留余量。
    password = fields.CharField(max_length=255, null=True, description="口令哈希")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    updated_at = fields.DatetimeField(auto_now=True, description="更新时间")
    deleted = fields.BooleanField(default=False, description="是否已删除")

    class Meta:
        table = "users"


class UserDao:
    """用户数据访问层"""

    @staticmethod
    async def create_user(
        username: str,
        password: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> User:
        """创建用户；nickname 默认等于 username（老账号迁移同规则）。"""
        user = await User.create(
            username=username,
            password=password,
            nickname=nickname if nickname is not None else username,
        )
        return user

    @staticmethod
    async def get_user_by_id(user_id: int) -> Optional[User]:
        """根据ID获取用户"""
        return await User.filter(id=user_id, deleted=False).first()

    @staticmethod
    async def get_user_by_username(username: str) -> Optional[User]:
        """根据用户名获取用户"""
        return await User.filter(username=username, deleted=False).first()

    @staticmethod
    async def update_user(user_id: int, **kwargs) -> Optional[User]:
        """更新用户信息"""
        updated_count = await User.filter(id=user_id, deleted=False).update(**kwargs)
        if updated_count:
            return await User.filter(id=user_id).first()
        return None

    @staticmethod
    async def delete_user(user_id: int) -> bool:
        """删除用户（软删除）"""
        updated_count = await User.filter(id=user_id, deleted=False).update(deleted=True)
        return bool(updated_count)

    # 注意：不再提供“把口令拼进 SQL 等值条件”的 login()。
    # 登录校验统一走 service 层：按用户名取用户 -> security.passwords.verify_password。

    @staticmethod
    async def list_users(skip: int = 0, limit: int = 100) -> list[User]:
        """获取用户列表"""
        return await User.filter(deleted=False).offset(skip).limit(limit).all()


# Pydantic模型用于API序列化；显式排除 password，避免哈希/明文被序列化到响应。
User_Pydantic = pydantic_model_creator(User, name="User", exclude=("password",))
UserIn_Pydantic = pydantic_model_creator(User, name="UserIn", exclude_readonly=True)