# Delta Service

三角洲行动（Delta Force）桌面客户端的后端服务：**FastAPI + Tortoise ORM + MySQL 8 + Caddy TLS**，单进程 asyncio 同时提供 HTTP API 与 WebSocket `/ws`。

当前已上线能力：JWT 账号体系、房间（公共/私有、房号、密码、成员上限、房主管理）、聊天历史、共享画板、技能 CD 共享、运营干员配置中心。生产入口：**https://deltachat.icu:8443**（Caddy 终结 TLS，反代容器 8000）。

> 文档与 `master@3b40a84` 对齐；接口字段以 [`PROTOCOL.md`](PROTOCOL.md) 为准，部署细节见 [`DEPLOYMENT.md`](DEPLOYMENT.md)，安全说明见 [`SECURITY.md`](SECURITY.md)。

---

## 1. 技术栈与架构

| 层 | 说明 |
|---|---|
| 语言/运行时 | Python 3.11+（镜像 `python:3.11-slim`） |
| HTTP | FastAPI；Swagger `/docs`、OpenAPI `/openapi.json`、健康检查 `/health` |
| WebSocket | FastAPI `/ws`，与 HTTP 共用 8000；旧独立 8765 仅 `LEGACY_WS_ENABLED=true` 时启动 |
| ORM/数据库 | Tortoise ORM + aiomysql，MySQL 8；启动时 `generate_schemas()` 建缺失表 |
| 认证 | JWT HS256，7 天有效，无 refresh token；Bearer（HTTP）/首消息 `auth`（WS） |
| 实时状态 | 在线连接、画板增量、技能共享缓存在进程内；单 worker 部署 |
| 部署 | Docker + GitHub Actions self-hosted runner；宿主机 Caddy 8443 → 8000 |

```text
Desktop Client ──HTTPS 8443──▶ Caddy ──HTTP 127.0.0.1:8000──▶ delta-service (FastAPI + /ws)
                                                               │
                                                               └── MySQL 8（users/rooms/messages/...）
```

## 2. 目录结构

```text
.
├── api/
│   ├── init.py              # FastAPI 应用工厂、路由注册、startup/shutdown
│   ├── deps.py              # Bearer JWT → 当前用户
│   ├── v1_common.py         # 统一响应包 / APIError / 错误处理
│   ├── v1_auth.py           # 登录/注册/我的资料/改密
│   ├── v1_rooms.py          # 房间 CRUD/join/leave/kick/mine/历史/画板/技能
│   ├── v1_config.py         # 公开干员配置中心
│   ├── ws.py                # FastAPI WebSocket /ws 适配
│   ├── room.py / user.py    # 旧版 /api 接口（过渡期保留，无 JWT）
├── dao/                     # Tortoise 模型 + DAO
│   ├── user.py room.py message.py canvas.py skill.py
├── service/
│   ├── user.py rooms.py messages.py canvas.py skills.py
│   ├── config_center.py backgrounds.py message_cleanup.py
│   ├── room.py websocket_service.py  # 旧内存房间/旧 WS（不参与 /ws）
├── websocket/websocket_manager.py   # /ws 协议引擎（认证/房间/聊天/画板/技能）
├── security/
│   ├── passwords.py         # scrypt 哈希/校验 + 旧明文兼容
│   ├── tokens.py            # JWT 签发/校验（HS256, leeway 60s）
│   └── login_limit.py       # 登录限频（IP 滑动窗口）
├── config/database.py       # Tortoise 配置（环境变量）
├── assets/
│   ├── operators.json       # 17 干员配置（配置中心数据源）
│   ├── operators.example.json
│   └── backgrounds/manifest.example.json
├── scripts/
│   ├── migrate_passwords.py # 旧明文口令迁移（幂等）
│   └── migrate_f.py         # F 迁移：users.nickname / rooms.deleted（幂等）
├── tests/                   # stdlib unittest，离线 stub，无需 DB
├── main.py                  # 入口：DB 初始化 + HTTP/WS + 清理任务
├── Dockerfile / .dockerignore
├── .github/workflows/deploy.yml
└── README / PROTOCOL / DEPLOYMENT / SECURITY / FEATURES
```

## 3. 环境变量

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `DB_HOST` | ✅ | – | MySQL 主机 |
| `DB_PORT` | ❌ | `3306` | |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | ✅ | – | 使用最小权限账号；`DB_CHARSET` 默认 `utf8mb4` |
| `JWT_SECRET` | ✅（认证） | – | ≥32 字节随机字符串；生产必须 ≥32 |
| `JWT_EXPIRE_DAYS` | ❌ | `7` | access token 有效期 |
| `FORWARDED_ALLOW_IPS` | 生产✅ | `127.0.0.1` | 信任的反代 IP；Caddy 场景 `172.17.0.1` |
| `LEGACY_WS_ENABLED` | ❌ | `false` | 旧 8765 WS/后台任务开关（默认关闭） |
| `WS_HOST` / `WS_PORT` | ❌ | `0.0.0.0` / `8765` | 仅 legacy server |
| `LOGIN_RATE_LIMIT_PER_MIN` | ❌ | `5` | 登录/分钟/IP |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | ❌ | `60` | 登录限频窗口 |
| `CONFIG_RATE_LIMIT_PER_MIN` | ❌ | `60` | 配置中心/分钟/IP |
| `MESSAGE_CLEANUP_INTERVAL_HOURS` | ❌ | `24` | 聊天 30 天 + 画板 7 天清理周期 |
| `OPERATORS_CONFIG_PATH` | ❌ | `assets/operators.json` | 配置中心文件路径 |
| `BACKGROUNDS_MANIFEST_PATH` | ❌ | `assets/backgrounds/manifest.json` | 底图 manifest 路径 |

`DB_*` 缺失时进程启动失败；`JWT_SECRET` 缺失时登录/注册/WS 认证会失败（HTTP 500/401）。

## 4. 本地开发与运行

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DB_HOST=127.0.0.1 DB_PORT=3306
export DB_USER=delta_app DB_PASSWORD='<password>' DB_NAME=delta_service
export JWT_SECRET='<至少 32 字节随机>'

python main.py
# HTTP: http://localhost:8000/docs   WS: ws://localhost:8000/ws
```

首次启动会自动建缺失表；**已有库升级必须执行迁移脚本**（`generate_schemas` 不会补列）：

```bash
python scripts/migrate_f.py
# 旧明文口令存量：python scripts/migrate_passwords.py（先 --dry-run）
```

### 测试

```bash
python -m unittest discover -s tests -v
```

测试全部离线（stdlib stub 替代 FastAPI/Tortoise/PyJWT/websockets），不连 MySQL；当前 418 个用例。

### Docker 本地运行

```bash
docker build -t delta-service .
docker run --rm -p 8000:8000 --env-file ./db.env \
  -e FORWARDED_ALLOW_IPS=172.17.0.1 delta-service
```

## 5. 功能清单（与线上一致）

### 账号 / 认证
- 注册：`POST /api/v1/auth/register`；用户名 6–20 位字母/数字且至少一个字母、大小写敏感；密码 **8–32**。
- 登录：`POST /api/v1/auth/login`；5 次/分/IP 限频（超限 429），JWT 7 天。
- 本人资料：`GET/PATCH /api/v1/users/me`（`username` 仅本人可见；昵称 1–20，实时生效）。
- 改密：`POST /api/v1/users/me/password`（旧密码错 `WRONG_OLD_PASSWORD`，新密码 8–32，不强制下线）。
- 对外展示统一 `nickname`；房间成员/聊天/WS 事件不暴露他人 `username`。

### 房间
- 创建：`POST /api/v1/rooms`；公共/私有，5 位房号（`[A-Z0-9]`），私有房 scrypt 密码（4–32），房主自动入房。
- 公共列表：`GET /api/v1/rooms`（cursor）；我的房间：`GET /api/v1/rooms/mine`（`joined_at`/`role`，过滤已关闭/软删）。
- 加入/退出：`POST /rooms/{id_or_code}/join|leave`；上限 **8 人**；退出摘除 WS 会话并广播。
- 房主：改名/改密（`PATCH`，1–30 字符；公开房不可设密码）、踢人（`POST .../kick`）、关闭/软删（`DELETE`，`deleted=true`，code 不复用）。
- WS：`join_room`、`user_joined/user_left`、`room_updated`、`room_deleted`、`room_kicked`（定向）。
- `/rooms/mine` 与成员列表只返回 nickname。

### 聊天
- WS `chat_send` → 房间内所有人（含自己）`chat_message`；`client_msg_id` 原样回传。
- 校验：content 1–2000、5 条/秒/用户限频；历史 `GET /rooms/{id_or_code}/messages`（7/30 天窗口、`window_days/truncated`）。
- 保留 30 天，由清理任务处理。

### 共享画板
- WS `canvas_op`（stroke/clear；坐标 0–1、≤512 点、30 条/秒/用户）→ 房间内他人；`rev` 连接级单调。
- 快照：`canvas_snapshot_save`（≤5000 笔/≤512KB，成员可写、last-writer-wins）；`room_joined.canvas_snapshot`、`GET /rooms/{id}/canvas` 返回 DB∪内存合并状态。
- 显式 save + 关房 flush；**不做自动快照**；快照保留 7 天。

### 技能共享（E1）
- WS `skill_share_update`（固定槽位 0 大招 / 1 技能 / 2 投掷物；`remaining_ms` 0–600000、`max_charges` 1–9、`pct` 0–100、`captured_at` int ms；2 条/秒/用户）。
- 服务端权威折算：`adjusted=max(0, remaining_ms-(now-captured_at))`，`ready_at=now+adjusted`（服务器时钟绝对毫秒）；`captured_at` 未来不放大、>10s 视为过期、负值归零。
- 广播 `skill_share_state`（含发送者）与 `room_joined.skill_states`；`GET /rooms/{id_or_code}/skills`；`skill_share_off` → `skill_share_removed`。
- 可选 `squad_number`（1/2/3，允许重复）随状态存储/下发；非法值忽略、不拒绝 payload；旧客户端不带该字段时为 NULL。
- 状态 30 分钟 stale 过滤；关房/软删清理；leave/kick 即使无 WS 连接也在 DB 删除。

### 配置中心 / 运营
- `GET /api/v1/config/operators`：**公开**，按 IP 限频（默认 60/分）；返回 `{version,etag,updated_at,operators,meta}`。
- 版本 = `assets/operators.json` 内容 sha256 前 16 位；响应头 `ETag`/`Cache-Control: public, max-age=60`；`If-None-Match` 匹配 304。
- 当前内置 **17 干员**；空/非法配置返回 **503 `CONFIG_UNAVAILABLE`**，绝不下发空列表。
- 改配置后重启容器生效（不做热加载）。

### 后台任务
- 聊天消息 30 天清理 + 画板快照 7 天清理，`MessageCleanupTask` 随应用 lifespan 启动（默认 24h，启动先跑一次）。
- 旧 `data_pusher`/离线检测仅在 `LEGACY_WS_ENABLED=true` 时运行。

## 6. API 与错误模型

- v1 成功：`{code:200,message:"OK",data:{...},request_id?}`；错误：`{code:4xxxx/5xxxx,message,data:{error_code},request_id?}`。
- 客户端精确分支读 `data.error_code`（如 `ROOM_NOT_FOUND`、`NOT_ROOM_MEMBER`、`ROOM_OWNER_MUST_CLOSE`、`INVALID_SKILL_PAYLOAD`、`CONFIG_UNAVAILABLE`、`RATE_LIMITED`）。
- 完整端点、字段、WS 事件、限频见 [`PROTOCOL.md`](PROTOCOL.md)。

## 7. 部署与运维

- 生产：Caddy 宿主机监听 **8443 TLS** → `reverse_proxy 127.0.0.1:8000`；证书为腾讯云静态证书（1 年，手动替换后 `systemctl restart delta-tls`）。
- CI（`.github/workflows/deploy.yml`）：self-hosted runner → build → smoke → 替换 `delta-service-container`（带 `-e FORWARDED_ALLOW_IPS=172.17.0.1`）→ `/health` 轮询。
- 升级数据库：部署后执行 `docker run --rm --env-file /etc/delta-service/db.env delta-service python scripts/migrate_f.py`（幂等）。
- 运维：`docker restart delta-service-container`（配置/代码更新后）；`docker logs -f delta-service-container` 看日志；登录日志应显示真实公网 IP（非 `172.17.0.1`）。
- 备份：**尚未自动化**（MySQL 备份/恢复演练列为待办；见 FEATURES）。
- 回滚：镜像未打版本标签，依赖部署机旧镜像 ID；详见 [DEPLOYMENT.md](DEPLOYMENT.md) §5。

## 8. 当前限制

- 单进程/单 worker：在线状态、画板增量、技能状态缓存和限频都在进程内，多实例需共享存储。
- 无自动画板快照（仅显式 save + 关房 flush）；无 undo。
- 聊天历史物理保留 30 天、历史展示按成员 7/30 天窗口；无全文搜索。
- 无 refresh token；`JWT_SECRET` 轮换会使全部 token 失效。
- 旧 `/api` 接口仍保留但无 JWT，仅过渡使用。
- WS 画板 rev 为连接级、重连重置；客户端以 `ready_at`/快照为准。

更多细节与测试矩阵见 [`FEATURES.md`](FEATURES.md)；安全约定见 [`SECURITY.md`](SECURITY.md)。
