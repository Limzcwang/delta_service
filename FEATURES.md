# 功能清单与已知技术债（FEATURES）

> 基线：`master@160c5a1`（本地工作副本提交 `d1443c6`）。本文逐条对照源码整理；每条功能都给出了代码依据（`文件:行号`），**没有在代码中找到实现的功能一律标为「未实现」或「部分实现」**。
>
> 状态定义：
>
> - **已实现**：代码中存在完整链路（HTTP/WS 可触达，或服务启动时实际执行）；
> - **部分实现**：主链路可用，但存在明确缺口（未接线、兼容性无保证、语义不完整、受路由/部署问题影响等）；
> - **未实现**：只有类/函数骨架或完全缺失，当前没有可触达的入口。
>
> 相关文档：[README.md](README.md)（接口/协议）、[SECURITY.md](SECURITY.md)、[DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 1. 房间模块

房间状态全部保存在进程内存（`service/room.py:61-63` 的 `ROOM_MANAGER` / `ROOM_PUBLIC`），没有数据库持久化。

| # | 功能 | 状态 | 代码依据 | 说明 |
|---|---|---|---|---|
| 1 | 创建房间 `POST /api/room/create` | 已实现 | `api/room.py:98-146`；`service/room.py:112-169` | 创建者自动 `join_room`；`domain_enum` 1=公共/2=私有 |
| 2 | 加入房间 `POST /api/room/join` | 已实现 | `api/room.py:149-202`；`service/room.py:172-201` | 成员与在线集合都是 `set`，重复加入幂等 |
| 3 | 离开房间 `POST /api/room/leave` | 已实现 | `api/room.py:205-247`；`service/room.py:204-241` | 只从 `online` 移除，**不从 `members` 移除** |
| 4 | 房间详情 `GET /api/room/info/{room_code}` | 已实现 | `api/room.py:250-292`；`service/room.py:75-89` | 返回 members/online 列表与创建时间 |
| 5 | 公共房间列表 `GET /api/room/public/list` | 已实现 | `api/room.py:295-335`；`service/room.py:244-268` | 无分页/过滤；只返回 `ROOM_PUBLIC` 中仍存在的房间 |
| 6 | 公共/私有房间区分 | 部分实现 | `service/room.py:92-109,143-145,255-258`；`const/str.py` | 仅有 `domain` 字符串标记与公共列表过滤；**没有加入权限校验**，知道 5 位房间码即可加入私有房间 |
| 7 | 房间码生成 | 已实现 | `service/room.py:69-72,128-157` | 5 位大写字母+数字，`random.choices`；冲突重试最多 10 次，超限抛 `ROOM_CODE_GENERATION_FAILED` |
| 8 | 成员列表维护 | 部分实现 | `service/room.py:183-186,219-228` | 成员只增不减（离开不删除），无法反映“已退出” |
| 9 | 在线状态维护 | 部分实现 | `service/room.py:183-186,222-226`；`websocket/websocket_manager.py:159-183` | 加入/离开/WS 断线会更新；HTTP 客户端离开后只移除 online；无房间级心跳超时清理 |
| 10 | 房间消息历史 | 部分实现 | `service/room.py:271-350`（`add_room_message` / `get_room_messages`，上限 100 条） | 函数已实现但**没有任何 HTTP/WS 调用方**；WS `room_message` 只广播不落历史（`service/websocket_service.py:245-305`） |
| 11 | 房间内消息广播（WS） | 已实现 | `websocket/websocket_manager.py:80-101`；`service/websocket_service.py:245-305` | 广播给房间 online 用户，默认包含发送者 |
| 12 | 不活跃房间清理 | 未实现（未接线） | `service/room.py:449-495` | `cleanup_inactive_rooms()` 无调用方，也没有定时任务 |
| 13 | 房间持久化 | 未实现 | `service/room.py:61-63` | 纯内存字典，进程重启全部丢失 |
| 14 | 删除房间 / 踢人 | 未实现 | – | 无对应 service/API 函数 |
| 15 | 房间管理权限（creator 管理） | 未实现 | – | `creator` 仅作为展示字段，没有权限判断 |
| 16 | 公共房间移除 | 部分实现 | `service/room.py:475-484` | 仅 `cleanup_inactive_rooms` 内部会从 `ROOM_PUBLIC` 移除，但该函数未接线 |

## 2. 用户模块

用户数据持久化在 MySQL `users` 表；口令以 scrypt 哈希存储（`dao/user.py:12`，`security/passwords.py`）。

| # | 功能 | 状态 | 代码依据 | 说明 |
|---|---|---|---|---|
| 1 | 创建用户 `POST /api/users/create` | 已实现 | `api/user.py:65-116`；`service/user.py:21-55` | 用户名 3–50 位、字母/数字/下划线；口令可选、≥6 位；写库前哈希 |
| 2 | 按 ID 查询 `GET /api/users/{user_id}` | 已实现 | `api/user.py:119-134`；`service/user.py:57-92` | 响应只含 id/username/created_at/updated_at |
| 3 | 按用户名查询 `GET /api/users/username/{username}` | 已实现 | `api/user.py:137-152`；`service/user.py:94-129` | 同上 |
| 4 | 更新用户 `PUT /api/users/{user_id}` | 已实现 | `api/user.py:155-173`；`service/user.py:131-172` | 支持 username/password；口令先哈希；API 层没有用户名格式校验（仅创建接口有） |
| 5 | 软删除 `DELETE /api/users/{user_id}` | 已实现 | `api/user.py:176-190`；`service/user.py:174-206`；`dao/user.py:47-51` | 仅置 `deleted=true`，行与口令哈希保留 |
| 6 | 登录 `POST /api/users/login` | 已实现 | `api/user.py:193-221`；`service/user.py:255-312` | 按用户名取用户 → `verify_password`；旧明文登录成功后立即重哈希 |
| 7 | 登录 token / session | 未实现 | `api/user.py:193-221`；`service/user.py:290-297` | 只返回用户 id/username，无 token/cookie；后续接口不校验登录态 |
| 8 | 用户列表 + 分页 `GET /api/users/list` | 部分实现 | `api/user.py:224-241`；`service/user.py:208-253` | skip/limit 无边界校验、无最大 limit；**且路由注册在 `/{user_id}` 之后（`api/user.py:119` vs `:224`），实际会被 `/{user_id}` 先匹配导致 422** |
| 9 | 在线状态维护 | 部分实现 | `service/user.py:11-13,315-344,391-442,486-543` | 内存 `USERS_ONLINE`；5 分钟无活动视为离线；单进程有效，重启丢失 |
| 10 | 在线用户列表 HTTP 接口 | 未实现 | `service/user.py:445-483` | `get_online_users()` 只有手动脚本调用，无 HTTP/WS 入口 |
| 11 | WS 绑定/解绑/强制下线辅助 | 未实现（未接线） | `service/user.py:546-664` | `bind_websocket_connection`/`unbind_websocket_connection`/`get_user_websocket`/`force_logout_user` 在生产链路中没有调用方 |
| 12 | 口令哈希 | 已实现 | `security/passwords.py`；`service/user.py:33,145,267-282` | scrypt + 随机 salt + 常量时间比较 |
| 13 | 存量明文口令迁移 | 已实现 | `scripts/migrate_passwords.py` | `--dry-run`、幂等、逻辑/IO 分离 |
| 14 | 用户资料扩展（邮箱/手机/头像） | 未实现 | `dao/user.py:7-17` | 模型只有 id/username/password/created_at/updated_at/deleted |
| 15 | 物理删除 / 数据清理 | 未实现 | `dao/user.py:47-51` | 只有软删除，无清理任务/注销流程 |

## 3. WebSocket 模块

WebSocket 入口是 FastAPI `/ws`（与 HTTP 共用 8000，`ws://<host>:8000/ws`）；旧独立 8765 server 由 `LEGACY_WS_ENABLED=true` 才启动，默认关闭。协议引擎的 `ping_interval=20s`、`ping_timeout=10s` 仍由旧 manager 使用，FastAPI 端点复用其会话/认证逻辑。

| # | 功能 | 状态 | 代码依据 | 说明 |
|---|---|---|---|---|
| 1 | 连接注册/断开管理 | 已实现 | `websocket/websocket_manager.py:25-73` | `connected_clients` 集合 + `user_websocket_map` 映射 |
| 2 | 首连欢迎消息 | 已实现 | `websocket/websocket_manager.py:44-50` | `type=system`、`clients_count`、`authenticated=false` |
| 3 | 身份认证 | 部分实现（弱） | `service/websocket_service.py:69-109`；`service/user.py:391-442` | 仅校验用户名是否在“HTTP 登录后的内存在线表”中；**不校验口令/token，可冒名在线用户**；认证成功会再次 `register` 并再发一次 system 消息 |
| 4 | 加入房间 | 已实现 | `service/websocket_service.py:111-181` | 要求已认证；广播 `user_joined` 并回 `join_success` |
| 5 | 离开房间 | 已实现 | `service/websocket_service.py:183-243` | 广播 `user_left`；异常时消息文本会回传 `str(e)`（已知业务异常，范围有限） |
| 6 | 房间消息广播 | 已实现 | `service/websocket_service.py:245-305` | 仅限当前房间；消息广播给房间 online（含发送者），回 `message_sent`；不落历史 |
| 7 | 全连接广播 | 已实现 | `service/websocket_service.py:316-329`；`websocket/websocket_manager.py:75-78` | `broadcast` 消息类型；**不要求认证** |
| 8 | echo 回显 | 已实现 | `service/websocket_service.py:307-314` | 原样返回 content |
| 9 | 心跳 | 已实现 | `service/websocket_service.py:331-333`；`websocket/websocket_manager.py:103-113,222-227` | 客户端 `ping`→`pong`；服务端每 30s 发 `heartbeat`；协议层 ping 20s/10s |
| 10 | 定时数据推送 | 部分实现 | `websocket/websocket_manager.py:185-201`；`main.py:112-117`；`websocket/websocket_manager.py:232-233` | 每 15s 推 `{type:"push", data_id, value}`；value 是模拟值（`20 + 10*(counter%10)` → 20–110）；**main 与 run_websocket_server 各启动一次，实际双倍推送** |
| 11 | 在线人数/连接数 | 部分实现 | `websocket/websocket_manager.py:44-50`；`service/websocket_service.py:154-166` | 首连消息有 `clients_count`、join 消息有 `online_count`；没有独立查询接口 |
| 12 | 错误消息 | 已实现 | `websocket/websocket_manager.py:140-153`；`service/websocket_service.py:74-77,114-127,256-268` | `type=error`；JSON 解析/处理失败带 `error_code`（`INVALID_JSON`/`MESSAGE_PROCESSING_ERROR`） |
| 13 | 断线清理 | 已实现 | `websocket/websocket_manager.py:155-183` | 断开时广播 `user_left`、更新在线状态、取消心跳任务 |
| 14 | 多房间 | 部分实现 | `service/websocket_service.py:20-23,132,219-220` | `current_room` 单值；用户可先后加入多个房间（members/online 集合可并存），但只记录“最近一个”房间 |
| 15 | 断线重连/会话恢复 | 未实现 | – | 无重连协议、无消息补发 |
| 16 | TLS/wss | 未实现 | `websocket/websocket_manager.py:222-227` | 只起明文 `ws://`；如需 TLS 需反向代理 |

## 4. 日志模块

| # | 功能 | 状态 | 代码依据 | 说明 |
|---|---|---|---|---|
| 1 | 文件 + 控制台日志 | 已实现 | `log/logger.py:10-49` | `logs/delta_service_YYYYmmdd_HHMMSS.log` + `StreamHandler` |
| 2 | 日志轮转 | 已实现 | `log/logger.py:37-44` | 10MB/文件，保留 5 个备份 |
| 3 | 日志级别配置 | 部分实现 | `log/logger.py:23` | 固定 `INFO`，无环境变量/配置开关 |
| 4 | 异常日志（traceback） | 已实现 | `log/logger.py:53-62` | `logger.error(..., exc_info=True)` |
| 5 | 业务事件日志 | 已实现 | `log/logger.py:66-76`；`service/room.py`、`service/user.py` 多处调用 | create/join/leave/login 等 |
| 6 | API 请求/响应日志 | 已实现 | `log/api_logger.py:14-90` | 方法/路径/状态/耗时/IP；跳过 health/docs 等；不记录 body |
| 7 | request_id 请求追踪 | 已实现 | `log/logger.py:179-190,270-297`；`log/api_logger.py:45-47`；`main.py:17-44` | `req_<12hex>`；500 响应与日志均带 `request_id` |
| 8 | WebSocket 事件日志 | 已实现 | `log/logger.py:98-113`；`websocket/websocket_manager.py:70,126` | 连接/断开事件；**`收到消息` 会记录原始 JSON（含聊天内容）** |
| 9 | 性能日志 | 未实现（未接线） | `log/logger.py:115-126` | `log_performance()` 无调用方 |
| 10 | SQL 日志中间件 | 部分实现 | `log/sql_logger.py:11-110,113-232` | 先尝试 `_query_middleware`，失败则方法重写；再失败只 `print` 提示，**能否生效取决于 Tortoise/驱动版本**；`main.py:79` 与 `api/init.py:61` 都会调用初始化，可能重复注册 |
| 11 | SQL 参数/错误脱敏 | 已实现 | `log/logger.py:131-192`；`log/sql_logger.py` 各日志路径 | 命中敏感标记整体替换为 `***REDACTED***` |
| 12 | 结构化/集中式日志 | 未实现 | – | 纯文本文件；无 JSON 格式、无采集/检索系统 |

## 5. 安全模块

| # | 功能 | 状态 | 代码依据 | 说明 |
|---|---|---|---|---|
| 1 | 口令 scrypt 哈希 | 已实现 | `security/passwords.py:19-129` | N=2¹⁴/r=8/p=1/dklen=32，16B salt，格式 `scrypt$N$r$p$salt$hash` |
| 2 | 旧明文兼容 + 登录即重哈希 | 已实现 | `security/passwords.py:98-129`；`service/user.py:267-282` | 常量时间比较；重哈希失败不阻断登录 |
| 3 | 存量迁移脚本 | 已实现 | `scripts/migrate_passwords.py` | `--dry-run`、幂等、含软删除用户 |
| 4 | API 响应排除 password | 已实现 | `dao/user.py:64`；`api/user.py:34-44,115,133,151,172,220,239` | `User_Pydantic exclude=("password",)` + `_PUBLIC_USER_FIELDS` 白名单 |
| 5 | 异常详情不外传 | 已实现 | `main.py:16-44`；`api/room.py:52-95`；`api/user.py:10-31` | 全局 500 只回 `code/message/request_id`；room/user 服务异常收敛为通用文案，详情只写日志 |
| 6 | 日志敏感字段脱敏 | 已实现 | `log/logger.py:131-192` | SQL 参数与 SQL error 字段 |
| 7 | Secret Scanning + Push Protection | 部分实现 | `.gitleaks.toml`；GitHub 仓库设置（仓库外） | 仓库内只有规则样例；**需在 GitHub Settings 开启**；custom patterns 未确认开启 |
| 8 | gitleaks pre-commit | 部分实现 | `.pre-commit-config.yaml` | 配置已提供，但需要开发者本机 `pre-commit install` 才生效；CI 未强制 |
| 9 | `.gitignore`/`.dockerignore` 防泄漏 | 已实现 | `.gitignore`；`.dockerignore` | 忽略 `.env*`、日志、IDE、私钥/证书等 |
| 10 | HTTP 接口鉴权 | 未实现 | `api/room.py`、`api/user.py` 全部端点 | 无 token/API key/依赖注入鉴权，任何人可调用 |
| 11 | WebSocket 强鉴权 | 未实现（仅弱校验） | `service/websocket_service.py:69-109` | 见 3.3 |
| 12 | CORS 安全配置 | 部分实现 | `api/init.py:21-28` | `allow_origins=["*"]` + `allow_credentials=True`，浏览器规范下组合不安全 |
| 13 | 速率限制/防爆破 | 未实现 | – | 登录无失败次数限制；API/WS 均无限流 |
| 14 | TLS（HTTPS/wss） | 未实现 | `main.py:54-59`、`websocket/websocket_manager.py:222-227` | 明文 HTTP/WS；需反向代理终结 TLS |
| 15 | 依赖漏洞扫描 | 未实现 | `requirements.txt` | 无 `pip-audit`/Dependabot 配置 |

## 6. 运维 / CI / 测试

| # | 功能 | 状态 | 代码依据 | 说明 |
|---|---|---|---|---|
| 1 | GitHub Actions self-hosted 部署 | 已实现 | `.github/workflows/deploy.yml` | push master / 手工触发；checkout 固定 SHA；preflight→build→smoke→deploy→health |
| 2 | 数据库凭据 env-file 注入 | 已实现 | `deploy.yml:17-21,34-42,75-98`；`DEPLOYMENT.md §2.5` | `--env-file /etc/delta-service/db.env`（0640），不进仓库/日志/命令行 |
| 3 | 手动兜底部署 | 已实现（部署机） | `DEPLOYMENT.md §4` | `/root/delta-deploy.sh` 不依赖 runner；脚本不在仓库 |
| 4 | 部署验证脚本 | 部分实现 | `check_deployment.sh:1-135` | 假设容器名/端口；WS 与 HTTP 共用 8000，脚本不再单独探端口；未覆盖新流程与 `/health` |
| 5 | Docker 镜像 | 已实现 | `Dockerfile:1-42`；`.dockerignore` | `python:3.11-slim`；`HEALTHCHECK` 用 `/health`；`EXPOSE 8000`（WS 与 HTTP 共用） |
| 6 | 健康检查 | 部分实现 | `api/init.py:48-51`；`Dockerfile:37-39` | `/health` 恒 200，不检查数据库/WS 依赖 |
| 7 | 自动化回滚 | 未实现 | `deploy.yml` | 镜像始终覆盖 `delta-service:latest`，无版本标签/发布记录；回滚靠本地旧镜像 ID |
| 8 | 多实例/水平扩展 | 未实现 | `service/room.py:61-63`；`service/user.py:11-13`；`main.py` | 状态在进程内存，多副本数据不一致；未使用 gunicorn/多 worker |
| 9 | 监控/告警 | 未实现 | – | 无 metrics/健康度上报/告警接入 |
| 10 | 备份/恢复 | 未实现 | – | 仓库无备份脚本或策略（旧 README 中的描述与代码不符） |
| 11 | 多环境配置 | 部分实现 | `config/database.py`；`.env.example` | 只有 `DB_*` 环境变量；无 APP_ENV/多环境 profiles |
| 12 | 数据库迁移框架 | 未实现 | `config/database.py:43-47` | 只有 `Tortoise.generate_schemas()` 建表，不做 ALTER；无 aerich/alembic |
| 13 | CI 运行单元测试 | 未实现 | `.github/workflows/deploy.yml` | 流水线只构建/冒烟/部署，不跑 `python -m unittest` |
| 14 | 依赖锁定/漏洞扫描 | 未实现 | `requirements.txt` | 使用 `~=` 范围版本，无 lock 文件/哈希 |
| 15 | 单元测试 | 已实现 | `tests/` | 84 个 stdlib unittest，离线可跑（见第 7 节） |

## 7. 测试现状

| 测试文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `tests/test_passwords.py` | 24 | scrypt 哈希/校验、legacy 明文、登录/创建/更新接线、API 序列化排除 password |
| `tests/test_password_migration.py` | 13 | 迁移计划、dry-run、4 用户迁移、幂等、空/已哈希行 |
| `tests/test_log_redaction.py` | 19 | SQL 参数脱敏、error 字段脱敏、日志格式 |
| `tests/test_sql_logger_redaction.py` | 5 | SQL 中间件日志脱敏、透传原始参数 |
| `tests/test_exception_handler.py` | 5 | 全局异常响应/request_id/traceback 只进日志 |
| `tests/test_exception_exposure.py` | 11 | room/user 异常映射、服务层通用文案、API 详情白名单 |
| `tests/test_room_exception_wrapping.py` | 7 | room 4 个 500 分支不泄漏原文、404/400 语义回归 |
| **合计** | **84** | `python -m unittest discover -s tests -v` 全绿；离线（`tests/support.py` 注入 fastapi/tortoise/uvicorn stub） |

测试缺口：

- 没有真实 MySQL / HTTP / WebSocket 集成测试；`test_websocket_refactor.py` 是需数据库的手动脚本，不在套件内；
- 没有覆盖路由顺序（如 `/api/users/list` 被遮蔽）、CORS、登录态等真实运行期行为；
- 无覆盖率统计、无 CI 强制。

---

## 8. 已知限制 / 技术债

> 每条都能在代码中定位；建议优先级为 P0（尽快）/P1（近期）/P2（有空再做）。

### 8.1 P0

**D-01 HTTP API 完全无鉴权**
`api/room.py`、`api/user.py` 所有端点都没有鉴权依赖；任何人都可以创建/更新/软删除用户、查询用户列表、创建/加入房间。
依据：`api/user.py:65-241`（无 Depends/中间件鉴权）、`api/room.py:98-335`。
影响：公网暴露即等于把用户管理接口开放给任何人。
建议：加 API key/JWT 依赖、管理接口与管理面分离、网关侧访问控制。

**D-02 WebSocket 认证可冒名**
`authenticate` 仅检查用户名是否在 `USERS_ONLINE`（HTTP 登录后 5 分钟内有效），不校验口令/token；任何知道在线用户名的人都能以该身份发消息/加入房间。
依据：`service/websocket_service.py:69-109`；`service/user.py:391-442`。
建议：登录签发短期 token（或 session id），WS 握手/首消息校验 token；`USERS_ONLINE` 与真实凭证解耦。

**D-03 房间/在线状态仅存内存，无法多实例/重启即丢**
`ROOM_MANAGER`、`ROOM_PUBLIC`、`USERS_ONLINE` 都是进程内全局字典；`main.py` 单进程 `asyncio.gather`。
依据：`service/room.py:61-63`；`service/user.py:11-13`；`main.py:103-123`。
影响：重启丢房间/在线态；多副本数据不一致；无法水平扩展。
建议：房间/成员/在线状态落 Redis（或 MySQL）并加过期策略；或明确产品定位为单实例。

### 8.2 P1

**D-04（已修复）WebSocket 单端口 `/ws`**
B2b 起 WS 通过 FastAPI `@router.websocket("/ws")` 与 HTTP 共用 8000，容器只需 `-p 8000:8000`；旧独立 8765 server 默认关闭（`LEGACY_WS_ENABLED=true` 才启动，不建议对公网开放）。

**D-05 重复后台任务：data_pusher / inactive_user_checker 各跑两份**
`main.py:112-117` 直接 gather 两个后台协程；`run_websocket_server()` 又 `create_task` 一次。
依据：`main.py:112-117`；`websocket/websocket_manager.py:232-233`。
影响：双倍推送（客户端每 15s 收两条）、双倍离线检测日志与锁竞争。
建议：只保留一处启动（建议 main 保留、manager 内删除）。

**D-06 SQL 日志初始化可能重复注册**
`main.init_services()` 与 `api/init.py` 的 startup 都会调用 `init_sql_logging()`。
依据：`main.py:79`；`api/init.py:61`；`log/sql_logger.py:113-149`。
影响：中间件被 append 两次 → 同一条 SQL 可能记录两条日志；也增加兼容性失败面。
建议：只保留一个初始化入口并做幂等保护。

**D-07 `/api/users/list` 被 `/{user_id}` 路由遮蔽**
依据：`api/user.py:119`（`GET /{user_id}`）在 `api/user.py:224`（`GET /list`）之前注册；FastAPI 按注册顺序匹配，`list` 会被当作 `user_id` → 422。
影响：文档中的用户列表接口实际不可用。
建议：把 `/list` 路由移到动态路由之前，或改成 `/api/users/list/all`。

**D-08 错误响应风格不统一**
房间接口返回裸 JSON，用户接口返回 `{code,message,data}`；异常时房间是 `{"detail":{error_code,message}}`，用户是 `{"detail":"字符串"}`，全局 500 是 `{code,message,request_id}`。
依据：`api/room.py:52-95,128-131,178-188`；`api/user.py:112-116,217-221`；`main.py:37-44`。
影响：客户端需要三套解析逻辑，易错。
建议：统一响应包（如 `{code,message,data,error_code?,request_id?}`），保留兼容期。

**D-09 房间消息历史/房间清理/在线用户等函数未接线**
`add_room_message`/`get_room_messages`/`cleanup_inactive_rooms`/`get_online_users`/`bind_websocket_connection` 等有实现但无生产调用方。
依据：`service/room.py:271-350,449-495`；`service/user.py:445-483,546-664`。
影响：看起来“有功能”，实际不可用；死代码增加维护成本。
建议：要么接入 HTTP/WS/定时任务，要么删除。

**D-10 私有房间无访问控制**
`domain=private` 只是标记且不进公共列表；`join_room` 不校验邀请码/密码/权限。
依据：`service/room.py:92-109,143-145,172-201`。
影响：知道 5 位房间码即可进入；5 位 A-Z0-9 空间约 6040 万，可枚举。
建议：私有房间加邀请码/密码或成员白名单，join 限流。

**D-11 无速率限制 / 无 TLS / CORS 不安全组合**
依据：`api/init.py:21-28`（`allow_origins=["*"]` + `allow_credentials=True`）；全项目无 rate limit/TLS 配置。
影响：登录爆破、接口滥用、浏览器侧凭据策略异常；明文传输。
建议：网关/反代层加 TLS、限流、CORS 白名单。

**D-12 无数据库迁移框架**
`init_database()` 只 `generate_schemas()`（建表），列变更不会自动应用；没有 aerich/alembic。
依据：`config/database.py:43-47`。
影响：模型改动（如 password 100→255）不会同步到既有库，只能手工 ALTER。
建议：引入 aerich（Tortoise 官方迁移工具）并纳入发布流程。

**D-13 CI 不跑单元测试**
`.github/workflows/deploy.yml` 只有 build/smoke/deploy，没有 `python -m unittest`；测试依赖开发者本地执行。
依据：`deploy.yml:28-107`。
影响：回归可能直接上线。
建议：加 test job（可离线跑）并设为 deploy 的 `needs`。

**D-14 回滚能力缺失（无版本化镜像）**
流水线每次覆盖 `delta-service:latest`，没有 git SHA/日期标签；回滚依赖本地旧镜像 ID 或重新构建。
依据：`deploy.yml:44-98`。
影响：事故回滚慢且不可重复。
建议：镜像打 `<git-sha>` + `latest` 双标签，保留最近 N 个版本；部署前记录镜像 ID。

### 8.3 P2

**D-15 健康检查不反映依赖状态**
`/health` 恒返回 healthy，不检查 MySQL/WS；Docker HEALTHCHECK 与部署 post-check 都只看这个端点。
依据：`api/init.py:48-51`；`Dockerfile:37-39`；`deploy.yml:88-97`。
建议：增加 `/ready` 检查 DB（`SELECT 1`），`/health` 仅进程存活。

**D-16 WS 原始消息写入日志（隐私）**
`logger.info(f"收到消息: {message}")` 会把聊天 JSON 落到日志文件/控制台。
依据：`websocket/websocket_manager.py:126`。
建议：只记录 type/长度/用户，不记录 content；或按日志级别开关。

**D-17 死代码/未使用模块较多**
`RoomAlreadyExistsError` 从未抛出（`service/room.py:29-33`，仅 `api/room.py:70-74` 处理）；`log_performance`、`GlobalLogger.websocket_connect/disconnect`、`SQLStatistics`、`APIStatistics`、`APILoggingConfig`、`disable/enable_sql_logging` 无调用。
依据：`service/room.py:29-33`；`log/logger.py:115-126,342-350`；`log/api_logger.py:125-226`；`log/sql_logger.py:235-303`。
建议：清理或接入。

**D-18 房间码/用户名等资源枚举面**
房间码 5 位 `random.choices`（非加密随机，`service/room.py:69-72`）；用户查询/创建接口无频率限制。
影响：可批量探测房间/用户；不是直接漏洞，但放大 D-01/D-10。
建议：`secrets.choice` + 私有房间邀请码 + 限流。

**D-19 用户软删除无数据清理策略**
只置 `deleted=true`，`users` 行与口令哈希永久保留；无注销/合规清理流程。
依据：`dao/user.py:47-51`。
建议：定义保留期与清理任务（或匿名化）。

**D-20 无监控/告警/集中日志**
日志只落本机文件（10MB×5），无 metrics、无错误率告警；容器重启/DB 断连只能靠人工发现。
依据：`log/logger.py:10-49`；`Dockerfile`；`deploy.yml`。
建议：接入 Prometheus/日志采集（或至少把 `docker logs` 接到集中平台）。

**D-21 依赖未锁定 / 无漏洞扫描**
`requirements.txt` 使用 `~=` 范围版本，无哈希锁定、无 Dependabot/pip-audit。
依据：`requirements.txt:1-9`。
建议：生成 lock（pip-tools/uv）并接入依赖扫描。

**D-22 性能日志/SQL 统计/请求统计未暴露**
相关类与函数存在但无 HTTP/metrics 出口。
依据：`log/api_logger.py:125-226`；`log/sql_logger.py:248-303`。
建议：要么接入 `/metrics`，要么删除。

---

## 9. 建议的下一步（Top 3）

1. **补鉴权体系（D-01/D-02/D-07 部分）**：HTTP 管理接口 + WS 连接统一使用登录签发的短期 token；这是当前最大的安全缺口，也决定能否公网部署。
2. **状态持久化与单进程问题（D-03/D-05/D-06）**：把房间/在线状态外置（Redis/MySQL），并修复后台任务重复启动，为多实例/滚动发布铺路。
3. **让部署与验证可信（D-04/D-13/D-14/D-15）**：WS 绑定地址修正、CI 增加单测与镜像版本标签、健康检查区分 liveness/readiness、补齐可回滚的发布流程。
