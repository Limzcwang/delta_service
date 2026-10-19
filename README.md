# Delta Service

基于 **FastAPI + websockets** 的实时通信服务：对外提供房间/用户 HTTP API 与 WebSocket 房间消息、心跳和定时推送。用户数据持久化在 MySQL；**房间、成员、在线状态只保存在进程内存**，进程重启即丢失；当前没有登录 token 或接口鉴权（详见 [FEATURES.md](FEATURES.md) 的「已知限制/技术债」）。

> 本文档与 `master@160c5a1`（含 3 个已合并安全 PR）的代码逐文件核对过。文档与代码冲突时以代码为准。

---

## 目录

- [1. 能力边界](#1-能力边界)
- [2. 技术栈与运行形态](#2-技术栈与运行形态)
- [3. 目录结构](#3-目录结构)
- [4. 快速开始](#4-快速开始)
- [5. API 参考（12 个业务接口 + /health）](#5-api-参考12-个业务接口--health)
- [6. 错误模型](#6-错误模型)
- [7. 日志系统](#7-日志系统)
- [8. WebSocket 协议](#8-websocket-协议)
- [9. 测试](#9-测试)
- [10. 部署与安全](#10-部署与安全)

---

## 1. 能力边界

| ✅ 已提供 | ❌ 未提供 |
|---|---|
| 房间创建/加入/离开/信息/公共列表 | 房间持久化（重启即丢） |
| 5 位房间码（A-Z0-9）、公共/私有标记 | 私有房间访问控制/邀请码 |
| 用户创建/查询/更新/软删除/登录/列表 | 登录 token、session、HTTP 鉴权 |
| 口令 scrypt 哈希存储 + 存量迁移脚本 | 邮箱/手机号/头像等资料字段 |
| WebSocket 连接管理、认证（弱）、房间消息广播、心跳、定时推送 | 真正的 WS 鉴权、断线重连、TLS/wss |
| 分级日志、SQL 日志、请求追踪 request_id、敏感字段脱敏 | 结构化/集中式日志、监控告警 |
| GitHub Actions self-hosted 部署、Docker 镜像 | 自动化回滚、多实例/水平扩展 |

## 2. 技术栈与运行形态

| 项 | 说明 |
|---|---|
| 语言/运行时 | Python 3.11+（镜像 `python:3.11-slim`） |
| HTTP 框架 | FastAPI（Swagger UI 在 `/docs`，OpenAPI 在 `/openapi.json`） |
| WebSocket | FastAPI `/ws`（与 HTTP 同端口：`ws://<host>:8000/ws`；旧独立 8765 server 默认关闭） |
| ORM | Tortoise ORM + aiomysql（MySQL） |
| 进程模型 | **单进程** asyncio：`main.py` 用 `asyncio.gather` 跑 HTTP+WS（同端口 8000）；旧 8765 server/推送/离线检测仅在 `LEGACY_WS_ENABLED=true` 时启动 |
| 持久化 | 仅 `users` 表（MySQL）；房间/消息历史/在线状态均在内存字典 |
| 部署 | Docker 镜像 + GitHub Actions self-hosted runner；生产 HTTPS 由 Caddy 反代 `https://deltachat.icu:8443`（见 [DEPLOYMENT.md](DEPLOYMENT.md)） |

> WebSocket 与 HTTP 共用 8000 端口（`/ws`），反代/防火墙只需处理 8000 一个业务端口。旧独立 8765 server 默认关闭（`LEGACY_WS_ENABLED=true` 才启动），不应再对公网开放。

## 3. 目录结构

```text
.
├── api/
│   ├── init.py              # FastAPI 应用工厂：CORS、日志中间件、Tortoise 注册、/health
│   ├── room.py              # 房间 5 个 HTTP 接口 + 房间异常→HTTP 映射
│   └── user.py              # 用户 7 个 HTTP 接口 + 错误文案白名单 / 用户字段白名单
├── service/
│   ├── room.py              # 房间业务逻辑（全部基于内存字典 ROOM_MANAGER / ROOM_PUBLIC）
│   ├── user.py              # 用户业务逻辑 + 内存在线状态 USERS_ONLINE + 口令哈希接线
│   └── websocket_service.py # WebSocket 消息分发（authenticate/join/leave/room_message/...）
├── dao/
│   └── user.py              # Tortoise User 模型 + UserDao；User_Pydantic 排除 password
├── websocket/
│   ├── __init__.py
│   └── websocket_manager.py # WS server、连接注册、广播、心跳、data_pusher、离线检测
├── log/
│   ├── logger.py            # 日志初始化（文件轮转/控制台）、各类日志函数、SQL 参数脱敏
│   ├── api_logger.py        # HTTP 请求/响应日志中间件、请求追踪 request_id
│   └── sql_logger.py        # SQL 执行日志中间件（Tortoise）
├── security/
│   └── passwords.py         # scrypt 口令哈希/校验（hash_password/verify_password/is_hashed）
├── scripts/
│   └── migrate_passwords.py # 存量明文口令迁移脚本（--dry-run、幂等）
├── config/
│   └── database.py          # Tortoise 配置（全部来自环境变量，缺失即启动失败）
├── const/
│   └── str.py               # 房间 domain 常量（public/private）
├── tests/                   # 84 个 stdlib unittest（离线运行，见第 9 节）
│   ├── support.py           # 离线 stub（fastapi/tortoise/uvicorn）与日志捕获工具
│   └── test_*.py
├── .github/workflows/deploy.yml  # 当前 CI/CD（self-hosted runner + Docker）
├── Dockerfile / .dockerignore
├── .env.example / .gitignore / .gitleaks.toml / .pre-commit-config.yaml
├── requirements.txt
├── main.py                  # 进程入口：初始化 DB，同时运行 HTTP/WS/推送/离线检测
├── check_deployment.sh      # 手动部署诊断脚本（docker/netstat/curl/logs）
└── test_websocket_refactor.py # 手动集成脚本（需数据库，不属于 unittest 套件）
```

## 4. 快速开始

### 4.1 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4.2 环境变量（必需）

`config/database.py` 在**导入时**读取环境变量；缺少任一必需项会直接抛 `DatabaseConfigError`，进程启动失败，不会回退到默认口令。

| 变量 | 必需 | 默认值 | 说明 |
|---|---|---|---|
| `DB_HOST` | ✅ | 无 | MySQL 主机 |
| `DB_PORT` | ❌ | `3306` | 非整数会在启动时报错 |
| `DB_USER` | ✅ | 无 | 应用账号（建议最小权限，不要 root） |
| `DB_PASSWORD` | ✅ | 无 | 口令（只在 env / env-file 中出现） |
| `DB_NAME` | ✅ | 无 | 数据库名，如 `delta_service` |
| `DB_CHARSET` | ❌ | `utf8mb4` | 连接字符集 |

```bash
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_USER=delta_app
export DB_PASSWORD='<你的口令>'
export DB_NAME=delta_service
# 或使用 env-file：set -a; source .env; set +a
```

`.env.example` 提供了模板；`.env*` 已被 `.gitignore` 忽略，**不要提交**。

### 4.3 启动

```bash
python main.py
```

启动后会同时运行：

| 服务 | 地址 | 说明 |
|---|---|---|
| 生产 HTTPS | <https://deltachat.icu:8443> | Caddy 终结 TLS，反代容器 8000；Swagger：<https://deltachat.icu:8443/docs> |
| HTTP API | <http://localhost:8000> | 本地直连；生产走 HTTPS |
| 健康检查 | <http://localhost:8000/health> | 恒返回 `{"status":"healthy","service":"delta_service"}`（不检查 DB） |
| WebSocket | `ws://localhost:8000/ws` | 与 HTTP 同端口；旧 8765 默认关闭 |

启动时会执行 `Tortoise.init` 并 `generate_schemas()`（建表，不做 ALTER/迁移）；数据库连不上时进程直接退出。

### 4.4 Docker（本地）

```bash
# 先参考 .env.example 创建 db.env（DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME）
docker build -t delta-service .
docker run --rm -p 8000:8000 --env-file ./db.env -e FORWARDED_ALLOW_IPS=172.17.0.1 delta-service
```

> WS 仍受 `localhost` 绑定限制；容器内 `docker logs` 可看到启动日志。生产流程见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 5. API 参考（12 个业务接口 + /health）

通用约定：

- **无鉴权**：所有 HTTP 接口不需要 token/API key；
- 请求体为 JSON，`Content-Type: application/json`；
- 响应由 FastAPI 直接序列化：**房间接口返回裸 JSON**，**用户接口返回 `{code,message,data}`（删除接口无 `data`）**，两套风格并存（见第 6 节）；
- `HTTPException(detail=...)` 在线上会被 FastAPI 包一层：`{"detail": <detail>}`；
- 参数校验失败（类型/缺失/约束）由 FastAPI 返回 **422**：`{"detail":[{"type":"...","loc":[...],"msg":"...","input":...}]}`。

### 5.1 房间接口（5 个）

前缀 `/api/room`。房间数据保存在内存，进程重启即丢失。

| # | 方法 | 路径 | 说明 |
|---|---|---|---|
| 1 | POST | `/api/room/create` | 创建房间，创建者自动加入 |
| 2 | POST | `/api/room/join` | 加入房间 |
| 3 | POST | `/api/room/leave` | 离开房间 |
| 4 | GET | `/api/room/info/{room_code}` | 房间详情 |
| 5 | GET | `/api/room/public/list` | 公共房间列表 |

#### 1）POST `/api/room/create`

请求：

```json
{"user_id": "u_1001", "domain_enum": 1}
```

`domain_enum`：`1`=公共（默认），`2`=私有，其它值 → 400 `INVALID_DOMAIN`。

成功 200：

```json
{"room_code": "A1B2C", "message": "房间创建成功"}
```

主要错误：

| 状态码 | error_code | 触发条件 |
|---|---|---|
| 400 | `INVALID_DOMAIN` | `domain_enum` 不是 1/2 |
| 500 | `ROOM_CODE_GENERATION_FAILED` | 连续 10 次生成到已存在的房间码 |
| 500 | `CREATE_ROOM_FAILED` | 创建过程中的内部异常（详情只进日志） |
| 500 | `INTERNAL_ERROR` + `request_id` | 未预期异常 |

错误体形态：`{"detail":{"error_code":"INVALID_DOMAIN","message":"无效的房间领域: 3"}}`。

#### 2）POST `/api/room/join`

```json
{"user_id": "u_1002", "room_code": "A1B2C"}
```

成功 200：

```json
{
  "message": "加入房间成功",
  "room_info": {
    "room_code": "A1B2C",
    "domain": "public",
    "creator": "u_1001",
    "members_count": 2,
    "online_count": 2,
    "created_at": 1750000000.0
  }
}
```

错误：`404 ROOM_NOT_FOUND`（房间不存在）、`500 JOIN_ROOM_FAILED`、`500 INTERNAL_ERROR`。重复加入是幂等的（成员用 `set`）。

#### 3）POST `/api/room/leave`

```json
{"user_id": "u_1002", "room_code": "A1B2C"}
```

成功 200：`{"message":"离开房间成功"}`

错误：`404 ROOM_NOT_FOUND`、**`400 USER_NOT_IN_ROOM`**（用户不在房间；异常映射把 UserNotInRoom 归到 400，尽管该路由的 access log 里记录的是 404）、`500 INTERNAL_ERROR`。

> 注意：离开只把用户从 `online` 移除，**不会从 `members` 移除**；成员列表只增不减。

#### 4）GET `/api/room/info/{room_code}`

成功 200：

```json
{
  "room_code": "A1B2C",
  "domain": "public",
  "creator": "u_1001",
  "members": ["u_1001", "u_1002"],
  "online": ["u_1001"],
  "created_at": 1750000000.0
}
```

错误：`404 ROOM_NOT_FOUND`、`500 INTERNAL_ERROR`。

#### 5）GET `/api/room/public/list`

成功 200：

```json
{"rooms": [{"room_code": "A1B2C", "domain": "public", "creator": "u_1001", "members": ["u_1001"], "online": ["u_1001"], "created_at": 1750000000.0}]}
```

只包含 `domain=public` 的房间；无分页/过滤参数。错误：`500 GET_PUBLIC_ROOMS_FAILED`、`500 INTERNAL_ERROR`。

### 5.2 用户接口（7 个）

前缀 `/api/users`。口令以 scrypt 哈希存库；响应显式排除 password。

| # | 方法 | 路径 | 说明 |
|---|---|---|---|
| 6 | POST | `/api/users/create` | 创建用户 |
| 7 | GET | `/api/users/{user_id}` | 按 ID 查 |
| 8 | GET | `/api/users/username/{username}` | 按用户名查 |
| 9 | PUT | `/api/users/{user_id}` | 更新用户名/口令 |
| 10 | DELETE | `/api/users/{user_id}` | 软删除 |
| 11 | POST | `/api/users/login` | 登录（校验口令，不发 token） |
| 12 | GET | `/api/users/list` | 用户列表+分页 ⚠️ 见下方路由问题 |

#### 6）POST `/api/users/create`

```json
{"username": "alice", "password": "Pa55w0rd!"}
```

校验：用户名 3–50 位、仅字母/数字/下划线；`password` 可选，提供时长度 ≥6。

成功 200：

```json
{"code": 200, "message": "用户创建成功", "data": {"id": 1, "username": "alice"}}
```

错误：400，`detail` 为字符串，如 `"用户名只能包含字母、数字和下划线"`、`"密码长度不能少于6个字符"`；服务层失败统一回退 `"创建用户失败"`。

#### 7）GET `/api/users/{user_id}`

成功 200：

```json
{"code": 200, "message": "获取用户信息成功", "data": {"id": 1, "username": "alice", "created_at": "2026-10-01T12:00:00", "updated_at": "2026-10-01T12:00:00"}}
```

错误：`404 "用户不存在"` / `"获取用户信息失败"`。

#### 8）GET `/api/users/username/{username}`

响应/错误同 #7。

#### 9）PUT `/api/users/{user_id}`

```json
{"username": "alice2", "password": "NewPa55w0rd!"}
```

两个字段都可选；`null` 字段被过滤。口令会先哈希再写库。成功 200：`{"code":200,"message":"用户信息更新成功","data":{"id":1,"username":"alice2"}}`；错误 400（`"更新用户信息失败"` 等）。用户名唯一性冲突会经 DB 异常 → 通用失败文案。

#### 10）DELETE `/api/users/{user_id}`

软删除（`deleted=true`，行保留）。成功 200：`{"code":200,"message":"用户删除成功"}`；错误 400（`"用户不存在或删除失败"`）。

#### 11）POST `/api/users/login`

```json
{"username": "alice", "password": "Pa55w0rd!"}
```

成功 200：

```json
{"code": 200, "message": "登录成功", "data": {"id": 1, "username": "alice"}}
```

错误：`401 "用户名或密码错误"` / `"登录失败"`。

> 登录只校验口令并把用户写入**进程内**在线表（5 分钟无活动视为离线），**不返回 token/session**，后续接口也不校验登录态。旧明文口令仍可登录，登录成功后自动重哈希（见 [SECURITY.md](SECURITY.md)）。

#### 12）GET `/api/users/list?skip=0&limit=100`

设计上返回：`{"code":200,"message":"获取用户列表成功","data":{"users":[{"id":1,"username":"alice","created_at":"...","updated_at":"..."}],"total":3}}`

> ⚠️ **已知路由缺陷**：该路由在 `api/user.py` 中注册于 `GET /api/users/{user_id}` **之后**（第 119 行 vs 第 224 行）。按 FastAPI/Starlette 的顺序匹配规则，`/api/users/list` 会先命中 `/{user_id}`，`"list"` 无法转成 `int` → 实际返回 **422**。需把 `/list` 路由提前或改成 `/api/users/list/all`。详见 [FEATURES.md](FEATURES.md)。

### 5.3 健康检查

GET `/health` → `{"status":"healthy","service":"delta_service"}`（Docker `HEALTHCHECK` 使用；仅表示进程在跑，不检查数据库/WS）。

## 6. 错误模型

当前存在三套错误形态（以代码为准）：

| 来源 | 形态 | 示例 |
|---|---|---|
| 房间业务异常（`api/room.py`） | HTTPException `detail` 为对象，被 FastAPI 包一层 | `{"detail":{"error_code":"ROOM_NOT_FOUND","message":"房间 ABC12 不存在"}}` |
| 用户接口校验/业务失败（`api/user.py`） | HTTPException `detail` 为字符串 | `{"detail":"用户名不能为空"}` |
| 全局未捕获异常（`main.py`） | 直接 JSONResponse，不含异常详情 | `{"code":500,"message":"服务器内部错误","request_id":"req_ab12cd34ef56"}` |
| FastAPI 参数校验 | 422 | `{"detail":[{"type":"int_parsing","loc":["path","user_id"],"msg":"...","input":"list"}]}` |

房间错误码 → 状态码（`api/room.py:52-95`）：

| error_code | 状态码 |
|---|---|
| `ROOM_NOT_FOUND` | 404 |
| `INVALID_DOMAIN` / `USER_NOT_IN_ROOM` | 400 |
| `ROOM_ALREADY_EXISTS` | 409（当前 service 层未主动抛出，属预留分支） |
| `CREATE_ROOM_FAILED` / `JOIN_ROOM_FAILED` / `LEAVE_ROOM_FAILED` / `GET_PUBLIC_ROOMS_FAILED` / `INTERNAL_ERROR` | 500 |

安全约定：未预期异常不返回 `str(e)`；完整异常（类型/消息/traceback）只写服务端日志，`request_id` 同时出现在响应和日志中，便于对账。

## 7. 日志系统

| 项 | 说明 |
|---|---|
| 位置 | 项目根目录 `logs/delta_service_YYYYmmdd_HHMMSS.log`（每次进程启动新建） |
| 轮转 | `RotatingFileHandler`，单文件 10MB，保留 5 个备份（`log/logger.py:37-44`） |
| 级别 | 固定 `INFO`（`log/logger.py:23`），无运行时开关 |
| 格式 | `%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s` |
| 输出 | 文件 + 控制台（`docker logs` 可见） |
| 日志类别 | 异常（含 traceback）、业务事件、API 请求/响应、WebSocket 事件、性能（函数存在但未接线）、SQL、系统事件、数据库连接 |

关键行为：

- **API 请求日志**：`log/api_logger.py` 中间件记录方法/路径/状态码/耗时/客户端 IP，不记录请求体与响应体；跳过 `/health`、`/docs`、`/redoc`、`/openapi.json`、`/favicon.ico`。
- **request_id**：中间件把 `req_<12hex>` 写入 `request.state.request_id`，全局 500 响应与日志都带该 ID（检索 `request_id:`）。
- **SQL 日志**：`log/sql_logger.py` 尝试挂接 Tortoise 查询中间件（失败时退化为方法重写/仅打印提示，不保证一定生效）。
- **脱敏**：SQL 文本命中 `password/passwd/pwd/token/secret/credential/api_key` 时，绑定参数整体替换为 `***REDACTED***`；SQL 的 `error` 字段命中同样标记也替换为 `***REDACTED***`（`log/logger.py:131-192`）。
- **WS 消息内容**：`websocket/websocket_manager.py:126` 会 `logger.info("收到消息: <原始 JSON>")`，即用户聊天内容会落日志（隐私风险，见 FEATURES）。

## 8. WebSocket 协议

- 地址：`ws://localhost:8000/ws`（与 HTTP 同端口；`ping_interval=20s`，`ping_timeout=10s`；旧 8765 默认关闭）
- 所有消息都是 JSON 文本；服务端消息均带 `type` 字段。
- **鉴权是弱校验**：客户端先发 `{"type":"authenticate","username":"alice"}`，服务端只检查该用户名是否在 HTTP 登录后的内存在线表中（5 分钟内活跃）。**不校验口令/token，任何人可冒用在线的用户名。**

### 8.1 连接后服务端主动消息

```json
{"type":"system","message":"连接成功","clients_count":1,"authenticated":false,"timestamp":"2026-10-01T12:00:00"}
```

收到 `authenticate` 成功后再发一次 `system`（`authenticated:true`）+ `auth_success`。

### 8.2 客户端 → 服务端

| type | 载荷 | 作用 | 是否要求已认证 |
|---|---|---|---|
| `authenticate` | `username` | 认证（仅校验在线状态） | 否 |
| `join_room` | `room_code` | 加入房间 | 是 |
| `leave_room` | `room_code`（缺省用当前房间） | 离开房间 | 是 |
| `room_message` | `content` | 向当前房间广播消息 | 是（且已加入房间） |
| `echo` | `content` | 原样回显 | 否 |
| `broadcast` | `content` | 广播给所有连接 | 否 |
| `ping` | – | 心跳，服务端回 `pong` | 否 |
| `activity` | – | 刷新在线活动时间 | 是 |
| 其它 | 任意 | 回 `ack` | 否 |

### 8.3 服务端 → 客户端（主要类型）

| type | 场景 | 示例要点 |
|---|---|---|
| `system` | 连接/认证成功 | `clients_count`、`authenticated` |
| `auth_success` / `auth_failed` | 认证结果 | `username` / `message` |
| `join_success` / `user_joined` | 加入房间成功/他人加入 | `room_info`、`username` |
| `leave_success` / `user_left` | 离开房间/他人离开 | `room_code` |
| `room_message` | 房间消息广播（含发送者） | `username`、`content`、`timestamp` |
| `message_sent` | 发送确认 | `message` |
| `broadcast` | 全连接广播 | `from`、`content` |
| `heartbeat` | 服务端每 30s 心跳 | `time` |
| `push` | 旧 8765 连接每 15s 定时推送（当前是模拟数据；`/ws` 不推送，且仅 `LEGACY_WS_ENABLED=true` 时存在） | `data_id`、`value`(20–110，模拟值)、`timestamp` |
| `pong` / `activity_ack` / `echo` / `ack` | ping、活动、回显、兜底 | – |
| `error` | 错误 | `message`，JSON/处理失败时带 `error_code` |

### 8.4 已知 WS 限制

- WS 入口是 FastAPI `/ws`（与 HTTP 共用 8000）；旧独立 8765 server 默认关闭，仅在 `LEGACY_WS_ENABLED=true` 时启动，且**不应再对公网开放**。
- 没有 token/会话恢复、没有断线重连协议、没有 wss（TLS 需反向代理）。
- 定时推送发送的是演示数据 `value=20 + 10*(counter%10)`，不是真实业务数据。
- `main.py` 与 `run_websocket_server()` 都启动了 `data_pusher`/`inactive_user_checker`，实际会**各跑两份**（双倍推送/检测）。

## 9. 测试

```bash
python -m unittest discover -s tests -v
```

- 当前 **84 个用例全绿**（Python 3.11/3.12 均验证过），**完全离线**：`tests/support.py` 用 `sys.modules` 注入 fastapi/tortoise/uvicorn 的最小 stub，不连数据库、不发网络请求。
- 覆盖：口令哈希与迁移、登录/创建/更新接线、API 序列化排除 password、异常不外传（room/user）、SQL 参数/错误脱敏、用户/房间异常映射、请求日志与 request_id 等。
- `test_websocket_refactor.py` 是**手动集成脚本**（需要真实 MySQL 与 `DB_*` 环境变量），不在 unittest 套件内，CI 也不会自动跑。

## 10. 部署与安全

生产 HTTPS：**https://deltachat.icu:8443**（Caddy 在宿主机终结 TLS，反代容器 `127.0.0.1:8000`）。容器必须设置 `FORWARDED_ALLOW_IPS=172.17.0.1` 并信任 `X-Forwarded-For`，否则登录限流会把所有用户当成同一来源；CI 的 deploy.yml 与手动 `docker run` 均已带该 env。部署后验证：`curl -fsS https://deltachat.icu:8443/health`，并确认登录日志来源 IP 为真实公网 IP（非 `172.17.0.1`）。

- 部署流程、self-hosted runner 升级、手动兜底、回滚与排障：[DEPLOYMENT.md](DEPLOYMENT.md)。
- 口令哈希、日志脱敏、Secret Scanning/gitleaks、历史清理状态、漏洞上报：[SECURITY.md](SECURITY.md)。
- 逐模块功能清单与已知技术债：[FEATURES.md](FEATURES.md)。

> 旧文档 `GITHUB_ACTIONS_SETUP.md` 描述的是已废弃的 SSH 部署方案，已删除，内容由 [DEPLOYMENT.md](DEPLOYMENT.md) 取代。
