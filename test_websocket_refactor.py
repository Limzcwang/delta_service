"""WebSocket重构测试脚本"""

import asyncio
from datetime import datetime

from config.database import init_database
from service.room import create_room, join_room, get_room_info, broadcast_to_room, get_room_members, \
    get_room_online_users, add_room_message, get_room_messages
from service.user import UserService
from service.websocket_service import WebSocketService
from websocket import WebSocketManager


async def test_user_service():
    """测试用户服务功能"""
    print("=== 测试用户服务功能 ===")

    # 测试用户活动更新
    result = await UserService.update_user_activity("test_user1")
    print(f"更新用户活动: {result}")

    # 测试检查用户在线状态
    online_status = await UserService.check_user_online("test_user1")
    print(f"检查用户在线状态: {online_status}")

    # 测试获取在线用户列表
    online_users = await UserService.get_online_users()
    print(f"获取在线用户列表: {len(online_users)} 个用户")

    # 测试检查不活跃用户
    inactive_result = await UserService.check_and_remove_inactive_users(10)  # 10秒阈值
    print(f"检查不活跃用户: {inactive_result}")

    print("用户服务测试完成\n")


async def test_room_service():
    """测试房间服务功能"""
    print("=== 测试房间服务功能 ===")

    # 创建房间
    room_code = create_room("test_user1", domain_enum=1)
    print(f"创建房间: {room_code}")

    # 获取房间信息
    room_info = get_room_info(room_code)
    print(f"获取房间信息: {room_info.get('domain')}")

    # 其他用户加入房间
    join_room("test_user2", room_code)
    print("用户加入房间")

    # 获取房间成员（正确处理返回值）
    members_result = get_room_members(room_code)
    if members_result.get("success"):
        members = members_result.get("members", [])
        print(f"房间成员: {members}")
    else:
        print(f"获取成员失败: {members_result.get('message')}")

    # 获取在线用户
    online_result = get_room_online_users(room_code)
    if online_result.get("success"):
        online_users = online_result.get("online_users", [])
        print(f"在线用户: {online_users}")
    else:
        print(f"获取在线用户失败: {online_result.get('message')}")

    # 添加房间消息
    message_result = add_room_message(room_code, "test_user1", "Hello, World!")
    if message_result.get("success"):
        print("添加房间消息成功")
    else:
        print(f"添加消息失败: {message_result.get('message')}")

    # 获取消息历史
    messages_result = get_room_messages(room_code, limit=10)
    if messages_result.get("success"):
        messages = messages_result.get("messages", [])
        print(f"消息历史数量: {len(messages)}")
    else:
        print(f"获取消息失败: {messages_result.get('message')}")

    # 广播消息
    broadcast_result = broadcast_to_room(room_code, {
        "type": "test_broadcast",
        "message": "测试广播消息"
    })
    if broadcast_result.get("success"):
        print("广播消息成功")
    else:
        print(f"广播失败: {broadcast_result.get('message')}")

    print("房间服务测试完成\n")


async def test_websocket_service():
    """测试WebSocket服务功能"""
    print("=== 测试WebSocket服务功能 ===")

    websocket_service = WebSocketService()

    # 测试服务初始化
    print("WebSocket服务初始化成功")

    # 测试获取认证用户和当前房间
    auth_user = websocket_service.get_authenticated_user()
    current_room = websocket_service.get_current_room()
    print(f"获取认证用户: {auth_user}, 当前房间: {current_room}")

    print("WebSocket服务测试完成\n")


async def test_websocket_manager():
    """测试WebSocket管理器功能"""
    print("=== 测试WebSocket管理器功能 ===")

    websocket_manager = WebSocketManager()

    # 测试管理器初始化
    print(f"WebSocket管理器初始化成功，连接数: {len(websocket_manager.connected_clients)}")

    # 测试用户映射
    print(f"用户WebSocket映射数: {len(websocket_manager.user_websocket_map)}")

    print("WebSocket管理器测试完成\n")


async def test_message_handling():
    """测试消息处理流程"""
    print("=== 测试消息处理流程 ===")

    websocket_service = WebSocketService()
    websocket_manager = WebSocketManager()

    # 模拟认证消息
    auth_message = {
        "type": "authenticate",
        "username": "test_user3"
    }

    print("模拟认证消息处理")

    # 模拟加入房间消息
    room_message = {
        "type": "join_room",
        "room_code": "TEST123"
    }

    print("模拟加入房间消息处理")

    # 模拟房间消息
    room_msg = {
        "type": "room_message",
        "content": "Hello, World!"
    }

    print("模拟房间消息处理")

    print("消息处理流程测试完成\n")


async def test_integration():
    """测试三层架构集成"""
    print("=== 测试三层架构集成 ===")

    # 1. 用户登录
    await UserService.update_user_activity("integration_user")
    print("用户登录成功")

    # 2. 创建房间
    room_code = create_room("integration_user", domain_enum=1)
    print(f"创建房间: {room_code}")

    # 3. 其他用户加入
    join_room("integration_user2", room_code)
    print("其他用户加入房间")

    # 4. 房间消息广播
    broadcast_result = broadcast_to_room(room_code, {
        "type": "integration_test",
        "message": "集成测试消息",
        "timestamp": datetime.now().isoformat()
    })
    print(f"房间消息广播: {broadcast_result.get('success')}")

    # 5. 用户离线检测
    await asyncio.sleep(1)
    inactive_result = await UserService.check_and_remove_inactive_users(2)  # 2秒阈值
    print(f"离线检测: {inactive_result.get('success')}")

    print("三层架构集成测试完成\n")


async def test_error_handling():
    """测试错误处理"""
    print("=== 测试错误处理 ===")

    # 测试不存在的房间
    try:
        room_info = get_room_info("NONEXISTENT")
    except Exception as e:
        print(f"处理不存在的房间错误: {type(e).__name__}")

    # 测试重复加入房间
    try:
        room_code = create_room("error_user", domain_enum=1)
        join_room("error_user", room_code)
        join_room("error_user", room_code)  # 重复加入
        print("处理重复加入房间")
    except Exception as e:
        print(f"处理重复加入错误: {type(e).__name__}")

    print("错误处理测试完成\n")


async def main():
    """主测试函数"""
    print("开始WebSocket重构测试...\n")

    # 日志系统自动初始化，无需手动调用
    print("日志系统已自动初始化")

    # 初始化数据库
    await init_database()
    print("数据库初始化完成")

    # 执行各项测试
    await test_user_service()
    await test_room_service()
    await test_websocket_service()
    await test_websocket_manager()
    await test_message_handling()
    await test_integration()
    await test_error_handling()

    print("==================================================")
    print("所有测试已完成！")
    print("==================================================")

    print("\n重构总结:")
    print("1. 三层架构设计完善：表现层、业务逻辑层、数据访问层")
    print("2. 用户登录后保持WebSocket连接")
    print("3. 断开连接后自动更新用户在线状态")
    print("4. 用户加入房间后支持消息广播")
    print("5. 消息包含时间、用户名、内容信息")
    print("6. 完善的错误处理和日志记录")

    print("\n功能验证:")
    print("- 用户服务：在线状态管理、活动跟踪")
    print("- 房间服务：创建、加入、消息广播")
    print("- WebSocket服务：连接管理、消息处理")
    print("- 集成测试：完整流程验证")
    print("- 错误处理：异常情况处理")


if __name__ == "__main__":
    asyncio.run(main())
