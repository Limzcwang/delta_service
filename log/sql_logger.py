"""SQL执行日志中间件"""

import time
from typing import Any, Dict, List, Optional

from tortoise.backends.base.client import BaseDBAsyncClient

from .logger import GlobalLogger, redact_sql_params


class SQLLoggerMiddleware:
    """SQL执行日志中间件"""

    def __init__(self):
        self.enabled = True

    async def execute_query(self, query: str, params: List[Any], db_client: BaseDBAsyncClient) -> List[Dict[str, Any]]:
        """执行查询并记录日志
        
        Args:
            query: SQL查询字符串
            params: 查询参数
            db_client: 数据库客户端
            
        Returns:
            查询结果
        """
        if not self.enabled:
            return await db_client.execute_query(query, params)

        # 记录SQL开始执行
        sql_context = GlobalLogger.sql_query(query, redact_sql_params(query, params))

        try:
            start_time = time.time()
            result = await db_client.execute_query(query, params)
            execution_time = time.time() - start_time

            # 记录成功执行
            GlobalLogger.sql_result(sql_context, True, "")

            return result

        except Exception as e:
            # 记录执行失败
            GlobalLogger.sql_result(sql_context, False, str(e))
            raise

    async def execute_inset(self, query: str, params: List[Any], db_client: BaseDBAsyncClient) -> Optional[int]:
        """执行插入操作并记录日志
        
        Args:
            query: SQL查询字符串
            params: 查询参数
            db_client: 数据库客户端
            
        Returns:
            插入的ID
        """
        if not self.enabled:
            return await db_client.execute_inset(query, params)

        # 记录SQL开始执行
        sql_context = GlobalLogger.sql_query(query, redact_sql_params(query, params))

        try:
            start_time = time.time()
            result = await db_client.execute_inset(query, params)
            execution_time = time.time() - start_time

            # 记录成功执行
            GlobalLogger.sql_result(sql_context, True, "")

            return result

        except Exception as e:
            # 记录执行失败
            GlobalLogger.sql_result(sql_context, False, str(e))
            raise

    async def execute_many(self, query: str, params: List[List[Any]], db_client: BaseDBAsyncClient) -> None:
        """执行批量操作并记录日志
        
        Args:
            query: SQL查询字符串
            params: 批量参数列表
            db_client: 数据库客户端
        """
        if not self.enabled:
            return await db_client.execute_many(query, params)

        # 记录SQL开始执行
        sql_context = GlobalLogger.sql_query(query, redact_sql_params(query, params))

        try:
            start_time = time.time()
            await db_client.execute_many(query, params)
            execution_time = time.time() - start_time

            # 记录成功执行
            GlobalLogger.sql_result(sql_context, True, "")

        except Exception as e:
            # 记录执行失败
            GlobalLogger.sql_result(sql_context, False, str(e))
            raise


# 全局SQL日志中间件实例
sql_logger_middleware = SQLLoggerMiddleware()


async def init_sql_logging():
    """初始化SQL日志记录
    
    为所有数据库连接添加SQL日志中间件
    """
    try:
        # 使用Tortoise.get_connection()获取默认连接
        from tortoise import Tortoise

        # 获取默认数据库连接
        try:
            connection = Tortoise.get_connection("default")
            print(f"成功获取数据库连接: {connection}")

            # 检查连接是否支持中间件
            if hasattr(connection, '_query_middleware'):
                connection._query_middleware.append(sql_logger_middleware)
                print("已为默认数据库连接添加SQL日志中间件")
            else:
                print("默认数据库连接不支持中间件，尝试其他方式")

                # 如果标准方式不支持，尝试直接记录SQL执行
                # 通过重写Tortoise的查询方法来实现日志记录
                await setup_sql_logging_alternative()

        except Exception as conn_error:
            print(f"获取数据库连接失败: {conn_error}")
            # 使用替代方案
            await setup_sql_logging_alternative()

        print("SQL日志记录初始化完成")

    except Exception as e:
        print(f"SQL日志记录初始化失败: {e}")
        import traceback
        print(f"详细错误信息: {traceback.format_exc()}")


async def setup_sql_logging_alternative():
    """设置SQL日志记录的替代方案
    
    通过重写Tortoise的查询方法来实现日志记录
    """
    try:
        from tortoise import Tortoise
        from tortoise.backends.base.client import BaseDBAsyncClient

        # 获取数据库客户端
        db_client = Tortoise.get_connection("default")._pool

        if hasattr(db_client, 'execute_query'):
            # 保存原始方法
            original_execute_query = db_client.execute_query
            original_execute_inset = getattr(db_client, 'execute_inset', None)
            original_execute_many = getattr(db_client, 'execute_many', None)

            # 重写execute_query方法
            async def logged_execute_query(query: str, params: list):
                """记录SQL查询执行"""
                sql_context = GlobalLogger.sql_query(query, redact_sql_params(query, params))

                try:
                    start_time = time.time()
                    result = await original_execute_query(query, params)
                    execution_time = time.time() - start_time

                    GlobalLogger.sql_result(sql_context, True, "")
                    return result

                except Exception as e:
                    GlobalLogger.sql_result(sql_context, False, str(e))
                    raise

            # 替换方法
            db_client.execute_query = logged_execute_query

            if original_execute_inset:
                async def logged_execute_inset(query: str, params: list):
                    """记录SQL插入执行"""
                    sql_context = GlobalLogger.sql_query(query, redact_sql_params(query, params))

                    try:
                        start_time = time.time()
                        result = await original_execute_inset(query, params)
                        execution_time = time.time() - start_time

                        GlobalLogger.sql_result(sql_context, True, "")
                        return result

                    except Exception as e:
                        GlobalLogger.sql_result(sql_context, False, str(e))
                        raise

                db_client.execute_inset = logged_execute_inset

            if original_execute_many:
                async def logged_execute_many(query: str, params: list):
                    """记录SQL批量执行"""
                    sql_context = GlobalLogger.sql_query(query, redact_sql_params(query, params))

                    try:
                        start_time = time.time()
                        result = await original_execute_many(query, params)
                        execution_time = time.time() - start_time

                        GlobalLogger.sql_result(sql_context, True, "")
                        return result

                    except Exception as e:
                        GlobalLogger.sql_result(sql_context, False, str(e))
                        raise

                db_client.execute_many = logged_execute_many

            print("已通过方法重写方式设置SQL日志记录")
        else:
            print("数据库客户端不支持标准查询方法，使用简化日志记录")

    except Exception as e:
        print(f"设置SQL日志记录替代方案失败: {e}")


async def disable_sql_logging():
    """禁用SQL日志记录"""
    sql_logger_middleware.enabled = False
    print("SQL日志记录已禁用")


async def enable_sql_logging():
    """启用SQL日志记录"""
    sql_logger_middleware.enabled = True
    print("SQL日志记录已启用")


# SQL执行统计类
class SQLStatistics:
    """SQL执行统计"""

    def __init__(self):
        self.total_queries = 0
        self.successful_queries = 0
        self.failed_queries = 0
        self.total_execution_time = 0.0
        self.slow_queries = []  # 记录慢查询

    def add_query(self, duration: float, success: bool = True, sql: str = ""):
        """添加查询统计
        
        Args:
            duration: 执行时间
            success: 是否成功
            sql: SQL语句
        """
        self.total_queries += 1
        self.total_execution_time += duration

        if success:
            self.successful_queries += 1
        else:
            self.failed_queries += 1

        # 记录慢查询（超过1秒）
        if duration > 1.0:
            self.slow_queries.append({
                "sql": sql[:200] + "..." if len(sql) > 200 else sql,
                "duration": duration,
                "timestamp": time.time()
            })

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息
        
        Returns:
            统计信息字典
        """
        avg_time = self.total_execution_time / self.total_queries if self.total_queries > 0 else 0

        return {
            "total_queries": self.total_queries,
            "successful_queries": self.successful_queries,
            "failed_queries": self.failed_queries,
            "success_rate": self.successful_queries / self.total_queries if self.total_queries > 0 else 0,
            "total_execution_time": self.total_execution_time,
            "average_execution_time": avg_time,
            "slow_query_count": len(self.slow_queries),
            "slow_queries": self.slow_queries[-10:]  # 返回最近10个慢查询
        }


# 全局SQL统计实例
sql_statistics = SQLStatistics()
