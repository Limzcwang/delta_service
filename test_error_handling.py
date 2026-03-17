#!/usr/bin/env python3
"""
错误处理和日志系统测试脚本

这个脚本用于测试项目的错误处理和日志系统是否正常工作
"""

import asyncio
import sys
import os
import time

# 添加项目路径到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service.room import (
    create_room, join_room, leave_room,
    get_room_info, get_public_room_list,
    RoomNotFoundError, UserNotInRoomError, InvalidDomainError, RoomServiceError
)
from log.logger import logger, log_api_request, log_exception, log_business_event


async def test_logging_system():
    """测试日志系统"""
    print("\n=== 测试日志系统 ===")
    
    # 测试不同级别的日志
    logger.debug("这是一条调试信息")
    logger.info("这是一条信息日志")
    logger.warning("这是一条警告信息")
    logger.error("这是一条错误信息")
    
    # 测试自定义日志函数
    try:
        raise ValueError("测试异常")
    except Exception as e:
        log_exception(e, "测试异常处理")
    
    log_api_request("GET", "/api/test", 200, "127.0.0.1", 0.123)
    log_business_event("test_event", "user123", "room456", {"detail": "测试事件"})
    
    print("✓ 日志系统测试完成")


async def test_room_service_errors():
    """测试房间服务的错误处理"""
    print("\n=== 测试房间服务错误处理 ===")
    
    # 测试创建房间
    try:
        room_code = create_room("test_user", domain_enum=1)
        print(f"✓ 创建房间成功: {room_code}")
        
        # 测试获取房间信息
        room_info = get_room_info(room_code)
        print(f"✓ 获取房间信息成功: {room_info['domain']}")
        
        # 测试加入房间
        join_room("test_user2", room_code)
        print("✓ 加入房间成功")
        
        # 测试离开房间
        leave_room("test_user2", room_code)
        print("✓ 离开房间成功")
        
    except Exception as e:
        print(f"✗ 房间操作失败: {e}")
    
    # 测试获取不存在的房间
    try:
        get_room_info("NON_EXISTENT")
        print("✗ 应该抛出RoomNotFoundError")
    except RoomNotFoundError as e:
        print(f"✓ 正确抛出RoomNotFoundError: {e}")
    except Exception as e:
        print(f"✗ 抛出错误的异常类型: {type(e).__name__}: {e}")
    
    # 测试无效的房间领域
    try:
        create_room("test_user", domain_enum=999)
        print("✗ 应该抛出InvalidDomainError")
    except InvalidDomainError as e:
        print(f"✓ 正确抛出InvalidDomainError: {e}")
    except Exception as e:
        print(f"✗ 抛出错误的异常类型: {type(e).__name__}: {e}")
    
    # 测试加入不存在的房间
    try:
        join_room("test_user", "NON_EXISTENT")
        print("✗ 应该抛出RoomNotFoundError")
    except RoomNotFoundError as e:
        print(f"✓ 正确抛出RoomNotFoundError: {e}")
    except Exception as e:
        print(f"✗ 抛出错误的异常类型: {type(e).__name__}: {e}")
    
    print("✓ 房间服务错误处理测试完成")


async def test_error_response_format():
    """测试错误响应格式"""
    print("\n=== 测试错误响应格式 ===")
    
    # 测试自定义异常的错误响应格式
    try:
        raise RoomNotFoundError("TEST_ROOM")
    except RoomNotFoundError as e:
        error_response = {
            "error_code": e.error_code,
            "message": e.message,
            "detail": "房间不存在"
        }
        print(f"✓ RoomNotFoundError响应格式: {error_response}")
    
    try:
        raise InvalidDomainError(999)
    except InvalidDomainError as e:
        error_response = {
            "error_code": e.error_code,
            "message": e.message,
            "detail": "无效的房间领域"
        }
        print(f"✓ InvalidDomainError响应格式: {error_response}")
    
    try:
        raise RoomServiceError("通用错误", "GENERAL_ERROR")
    except RoomServiceError as e:
        error_response = {
            "error_code": e.error_code,
            "message": e.message,
            "detail": "服务器内部错误"
        }
        print(f"✓ RoomServiceError响应格式: {error_response}")
    
    print("✓ 错误响应格式测试完成")


async def test_performance_logging():
    """测试性能日志记录"""
    print("\n=== 测试性能日志记录 ===")
    
    # 模拟性能测试
    start_time = time.time()
    
    # 模拟一些操作
    await asyncio.sleep(0.1)
    
    duration = time.time() - start_time
    
    # 记录性能日志
    logger.info(f"性能监控 - 模拟操作 - 耗时:{duration:.3f}s - 大小:1024")
    
    print(f"✓ 性能日志记录完成 - 耗时: {duration:.3f}s")


async def test_websocket_logging():
    """测试WebSocket日志记录"""
    print("\n=== 测试WebSocket日志记录 ===")
    
    # 模拟WebSocket事件日志
    logger.info("WebSocket事件 - connect - 客户端:client123 - 当前在线:5")
    logger.info("WebSocket事件 - message - 客户端:client123 - 消息类型:echo")
    logger.info("WebSocket事件 - disconnect - 客户端:client123 - 当前在线:4")
    
    print("✓ WebSocket日志记录完成")


async def run_all_tests():
    """运行所有测试"""
    print("开始错误处理和日志系统测试...")
    
    try:
        await test_logging_system()
        await test_room_service_errors()
        await test_error_response_format()
        await test_performance_logging()
        await test_websocket_logging()
        
        print("\n🎉 所有测试完成！")
        print("\n检查日志文件 logs/ 目录下是否有新的日志文件生成")
        print("日志文件格式: delta_service_YYYYMMDD_HHMMSS.log")
        
    except Exception as e:
        print(f"\n❌ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_all_tests())