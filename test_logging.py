#!/usr/bin/env python3
"""
日志系统测试脚本

这个脚本用于测试项目的日志系统是否正常工作
"""

import sys
import os

# 添加项目路径到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from log.logger import logger, log_exception, log_business_event, log_api_request, log_websocket_event, log_performance


def test_basic_logging():
    """测试基础日志功能"""
    print("=== 测试基础日志功能 ===")
    
    # 测试不同级别的日志
    logger.debug("这是一条调试信息")
    logger.info("这是一条信息日志")
    logger.warning("这是一条警告信息")
    logger.error("这是一条错误信息")
    
    print("✓ 基础日志测试完成")


def test_custom_log_functions():
    """测试自定义日志函数"""
    print("\n=== 测试自定义日志函数 ===")
    
    # 测试异常记录
    try:
        raise ValueError("测试异常")
    except Exception as e:
        log_exception(e, "测试异常处理", {"user_id": "test_user", "room_code": "ABC123"})
    
    # 测试业务事件记录
    log_business_event("create_room", "user123", "ABC123", {"domain": "public"})
    log_business_event("join_room", "user456", "ABC123", {"timestamp": "2023-01-01"})
    
    # 测试API请求记录
    log_api_request("POST", "/api/room/create", 200, "127.0.0.1", 0.123)
    log_api_request("GET", "/api/room/info/ABC123", 404, "127.0.0.1", 0.045)
    
    # 测试WebSocket事件记录
    log_websocket_event("connect", 5, "client123", "连接成功")
    log_websocket_event("message", 5, "client123", "消息类型:echo")
    log_websocket_event("disconnect", 4, "client123", "连接断开")
    
    # 测试性能监控记录
    log_performance("房间创建", 0.045, 1024, "user123")
    log_performance("消息处理", 0.012, 256, "user456")
    
    print("✓ 自定义日志函数测试完成")


def test_log_file_creation():
    """测试日志文件创建"""
    print("\n=== 测试日志文件创建 ===")
    
    import os
    
    # 检查logs目录是否存在
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    if os.path.exists(logs_dir):
        print(f"✓ logs目录存在: {logs_dir}")
        
        # 检查日志文件
        log_files = [f for f in os.listdir(logs_dir) if f.endswith('.log')]
        if log_files:
            print(f"✓ 找到日志文件: {log_files}")
            
            # 读取最新的日志文件内容
            latest_log = os.path.join(logs_dir, log_files[-1])
            with open(latest_log, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                print(f"✓ 日志文件包含 {len(lines)} 行记录")
                
                # 显示最后几行日志
                print("\n最后5行日志内容:")
                for line in lines[-5:]:
                    print(f"  {line.strip()}")
        else:
            print("✗ 未找到日志文件")
    else:
        print("✗ logs目录不存在")


def main():
    """主函数"""
    print("开始测试日志系统...\n")
    
    try:
        test_basic_logging()
        test_custom_log_functions()
        test_log_file_creation()
        
        print("\n🎉 日志系统测试完成！")
        print("\n请检查以下内容:")
        print("1. 控制台是否显示了所有日志消息")
        print("2. logs/目录下是否生成了新的日志文件")
        print("3. 日志文件内容是否包含所有测试记录")
        
    except Exception as e:
        print(f"\n❌ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()