"""数据库配置：全部来自环境变量，缺任何一个必需变量就启动失败（不提供默认口令）。"""
import os

from tortoise import Tortoise


class DatabaseConfigError(RuntimeError):
    """数据库配置缺失或非法。"""


def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise DatabaseConfigError(f"缺少必需的环境变量: {name}")
    return value.strip()


DATABASE_CONFIG = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": _require("DB_HOST"),
                "port": int(os.getenv("DB_PORT", "3306")),
                "user": _require("DB_USER"),
                "password": _require("DB_PASSWORD"),
                "database": _require("DB_NAME"),
                "charset": os.getenv("DB_CHARSET", "utf8mb4"),
            },
        }
    },
    "apps": {
        "models": {
            "models": ["dao.user", "dao.room", "dao.message"],
            "default_connection": "default",
        }
    },
    "use_tz": False,
    "timezone": "Asia/Shanghai",
}


async def init_database():
    """初始化数据库连接"""
    await Tortoise.init(config=DATABASE_CONFIG)
    # 生成数据库表结构
    await Tortoise.generate_schemas()


async def close_database():
    """关闭数据库连接"""
    await Tortoise.close_connections()
