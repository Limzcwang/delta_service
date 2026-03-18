from tortoise import Tortoise


# 数据库配置
DATABASE_CONFIG = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": "localhost",
                "port": 3306,
                "user": "root",
                "password": "Lim@241669",
                "database": "delta_service",
                "charset": "utf8mb4",
            }
        }
    },
    "apps": {
        "models": {
            "models": ["dao.user", "aerich.models"],
            "default_connection": "default",
        }
    },
    "use_tz": False,
    "timezone": "Asia/Shanghai"
}


async def init_database():
    """初始化数据库连接"""
    await Tortoise.init(config=DATABASE_CONFIG)
    # 生成数据库表结构
    await Tortoise.generate_schemas()


async def close_database():
    """关闭数据库连接"""
    await Tortoise.close_connections()