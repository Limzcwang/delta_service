# Delta Service - 实时通信服务

这是一个基于Python的实时通信服务，同时提供WebSocket实时通信和HTTP REST API接口。

## 功能特性

- **WebSocket服务**: 实时消息推送、心跳检测、广播功能
- **HTTP API**: 房间管理、用户管理、数据查询
- **房间管理**: 支持公共房间和私有房间
- **实时通信**: 支持多用户实时聊天和数据推送
- **完善的错误处理**: 自定义异常类、详细的错误响应
- **强大的日志系统**: 文件轮转、业务事件记录、性能监控

## 项目结构

```
delta_service/
├── api/                 # HTTP API层
│   ├── init.py         # API应用初始化
│   └── room.py         # 房间管理API
├── service/            # 业务逻辑层
│   └── room.py         # 房间管理服务
├── const/              # 常量定义
│   └── str.py          # 字符串常量
├── log/                # 日志管理
│   └── logger.py       # 日志配置
├── main.py             # 主入口文件
├── test_error_handling.py # 错误处理测试脚本
└── requirements.txt    # 项目依赖
```

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务

```bash
python main.py
```

服务启动后：
- WebSocket服务运行在: `ws://localhost:8765`
- HTTP API服务运行在: `http://localhost:8000`
- API文档访问: `http://localhost:8000/docs`

## 测试错误处理系统

项目提供了完整的测试脚本来验证错误处理和日志系统：

```bash
python test_error_handling.py
```

测试脚本会验证以下功能：
- ✅ 日志系统（不同级别日志、自定义日志函数）
- ✅ 房间服务错误处理（自定义异常抛出和捕获）
- ✅ 错误响应格式（标准化的错误响应）
- ✅ 性能日志记录（操作耗时监控）
- ✅ WebSocket日志记录（连接事件记录）

测试完成后，检查 `logs/` 目录下生成的日志文件，验证日志系统是否正常工作。

## API接口文档

### 房间管理API

#### 1. 创建房间
- **URL**: `POST /api/room/create`
- **参数**:
  ```json
  {
    "user_id": "用户ID",
    "domain_enum": 1
  }
  ```
- **响应**:
  ```json
  {
    "room_code": "ABC12",
    "message": "房间创建成功"
  }
  ```
- **错误码**:
  - `INVALID_DOMAIN`: 无效的房间领域
  - `ROOM_CODE_GENERATION_FAILED`: 无法生成唯一房间码

#### 2. 加入房间
- **URL**: `POST /api/room/join`
- **参数**:
  ```json
  {
    "user_id": "用户ID",
    "room_code": "房间码"
  }
  ```
- **错误码**:
  - `ROOM_NOT_FOUND`: 房间不存在

#### 3. 离开房间
- **URL**: `POST /api/room/leave`
- **参数**: 同加入房间
- **错误码**:
  - `ROOM_NOT_FOUND`: 房间不存在
  - `USER_NOT_IN_ROOM`: 用户不在房间中

#### 4. 获取房间信息
- **URL**: `GET /api/room/info/{room_code}`
- **响应**:
  ```json
  {
    "room_code": "ABC12",
    "domain": "public",
    "creator": "user123",
    "members": ["user123", "user456"],
    "online": ["user123"],
    "created_at": 1700000000.0
  }
  ```

#### 5. 获取公共房间列表
- **URL**: `GET /api/room/public/list`

## 错误处理系统

### 自定义异常类

项目定义了完整的异常处理体系：

- `RoomServiceError`: 房间服务基础异常
- `RoomNotFoundError`: 房间不存在异常
- `UserNotInRoomError`: 用户不在房间异常
- `InvalidDomainError`: 无效的房间领域异常
- `RoomAlreadyExistsError`: 房间已存在异常

### 错误响应格式

所有API错误都返回标准化的错误响应：

```json
{
  "error_code": "ROOM_NOT_FOUND",
  "message": "房间 ABC12 不存在",
  "detail": "详细错误信息（可选）"
}
```

### HTTP状态码映射

- `200`: 成功
- `400`: 请求参数错误
- `404`: 资源不存在
- `409`: 资源冲突
- `500`: 服务器内部错误

## 日志系统

### 日志配置

- **日志级别**: INFO
- **输出目标**: 控制台 + 文件
- **文件轮转**: 最大10MB，保留5个备份
- **日志格式**: 时间戳 - 服务名 - 级别 - 文件:行号 - 消息

### 日志文件位置

日志文件保存在 `logs/` 目录下，文件名格式：`delta_service_YYYYMMDD_HHMMSS.log`

### 自定义日志函数

项目提供了丰富的日志记录函数，使用示例如下：

```python
# 异常记录
logging.error("操作失败: %s", str(exception), exc_info=True)

# 业务事件记录
logging.info("业务事件 - create_room - 用户:user123 - 房间:ABC12")

# API请求记录
logging.info("API请求 - POST /api/room/create - 状态:200 - IP:127.0.0.1 - 耗时:0.123s")

# WebSocket事件记录
logging.info("WebSocket事件 - connect - 客户端:client123 - 当前在线:5")

# 性能监控记录
logging.info("性能监控 - 房间创建 - 耗时:0.045s - 大小:1024")
```

### 日志类型

1. **系统日志**: 服务启动、停止等系统事件
2. **业务日志**: 房间创建、用户加入等业务事件
3. **API日志**: HTTP请求和响应记录
4. **WebSocket日志**: 连接、断开、消息处理事件
5. **异常日志**: 详细的错误堆栈信息
6. **性能日志**: 操作耗时和数据大小

## WebSocket消息格式

### 连接消息
```json
{
  "type": "system",
  "message": "连接成功",
  "clients_count": 1
}
```

### 心跳消息
```json
{
  "type": "heartbeat",
  "time": "2023-01-01T12:00:00"
}
```

### 数据推送
```json
{
  "type": "push",
  "data_id": 1,
  "value": 25.5,
  "timestamp": "2023-01-01T12:00:00"
}
```

### 错误消息
```json
{
  "type": "error",
  "message": "消息处理失败",
  "error_code": "MESSAGE_PROCESSING_ERROR"
}
```

## 开发说明

### 错误处理最佳实践

1. **服务层**: 抛出具体的自定义异常
2. **API层**: 捕获异常并转换为HTTP响应
3. **全局处理**: 未捕获的异常由全局异常处理器处理

### 日志记录最佳实践

1. **关键操作**: 记录业务事件和性能指标
2. **异常情况**: 记录详细的错误信息和上下文
3. **API请求**: 记录请求路径、状态码和耗时
4. **WebSocket**: 记录连接状态和消息处理

## 技术栈

- **Web框架**: FastAPI
- **WebSocket**: websockets
- **异步**: asyncio
- **文档**: OpenAPI (自动生成)
- **服务器**: uvicorn
- **日志**: logging + RotatingFileHandler