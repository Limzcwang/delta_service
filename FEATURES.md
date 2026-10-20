# 功能清单与里程碑状态（FEATURES）

> 基线：`master@3b40a84`（含 B/C/D/E1、F、H1–H3、HTTPS 与配置中心）。测试：412 个 stdlib unittest 全离线通过。文档与代码冲突时以代码 / [PROTOCOL.md](PROTOCOL.md) 为准。

## 1. 里程碑总览

| 里程碑 | 内容 | 状态 |
|---|---|---|
| A | 稳定性修复、日志/异常暴露收敛、WS 绑定 | ✅ 已完成 |
| B1/B2 | JWT 认证；房间模型 + v1 HTTP；WS 认证/房间频道 | ✅ 已完成 |
| C | 聊天持久化 + `chat_send` 广播 + 历史分页 + 30 天清理 | ✅ 已完成 |
| D | 共享画板：增量广播、快照、历史合并、关房/踢人清理 | ✅ 已完成 |
| E1 | 技能 CD 共享：槽位固定、服务端 `ready_at` 折算、`room_joined.skill_states`、HTTP GET、30 分钟 stale | ✅ 已完成 |
| E2 | 运营干员配置中心：17 干员、版本/ETag/304、空配置 503、公开限频 | ✅ 已完成 |
| E3 | HUD 技能识别 / CD 共享（客户端识别 + 服务端共享） | 🟡 部分完成（服务端已完成上线；客户端 0.1.4 已完成；素材/实验后续优化） |
| E4 | 自动采集 / 自动共享循环 | 🟡 部分完成（客户端自动循环已完成；服务端无需改动；覆盖/精度后续优化） |
| F | 用户资料（nickname/隔离 username/改密）、房间软删除、房主改房、历史 7/30 天窗口 | ✅ 已完成 |
| H1 | 「我的房间」+ 退出语义、路由顺序、游标分页 | ✅ 已完成 |
| H2 | 密码上限收紧：账号 8–32、私有房 4–32 | ✅ 已完成 |
| H3 | 房主踢人定向 `room_kicked` + 会话摘除 | ✅ 已完成 |
| — | 生产 HTTPS（Caddy 8443、证书、真实 IP 透传） | ✅ 已上线 |

---

### 1.1 E3/E4 完成度细分

| 项 | 状态 | 说明 |
|---|---|---|
| E3 服务端字段与事件 | ✅ 已完成（已上线） | `skill_share_update`、服务端权威 `ready_at` 折算、`room_joined.skill_states`、`GET /rooms/{code}/skills`；30 分钟 stale 过滤 |
| E3 客户端 HUD 识别 | ✅ 已完成（0.1.4） | 事件驱动：亮度/充能跳变 + 文字秒数；**不依赖技能图标模板** |
| E3 其他干员素材补齐 | ⏳ 后续优化 | 非服务端阻塞项 |
| E3 灰带比例实验 | ⏳ 后续优化 | 识别精度实验，后续迭代 |
| E4 客户端自动采集/共享 | ✅ 已完成 | 2 FPS 采样 + 异步识别 + 状态变化上报 |
| E4 服务端改动 | ✅ 无需改动 | 现有 `skill_share_*` 协议已满足 |
| E4 更多干员覆盖与精度调优 | ⏳ 后续优化 | 持续迭代项 |

> 结论：E3/E4 的主体功能已完成并上线，**不是服务端功能缺失**；剩余仅为客户端素材/识别实验与覆盖精度优化。

## 2. 账号 / 认证

| 功能 | 状态 | 说明 |
|---|---|---|
| 注册 | ✅ | `POST /api/v1/auth/register`；用户名 6–20 位 `[A-Za-z0-9]` 且至少一个字母、大小写敏感；密码 **8–32** |
| 登录 | ✅ | JWT HS256 7 天、无 refresh；5 次/分/IP 限频 |
| 当前用户 | ✅ | `GET /api/v1/users/me`；本人可见 username+nickname |
| 改昵称 | ✅ | `PATCH /api/v1/users/me`；1–20 字符、去空格/控制字符、不唯一；WS 发送前刷新 DB，无需重连 |
| 改密码 | ✅ | `POST /api/v1/users/me/password`；旧密错 `WRONG_OLD_PASSWORD`；新密 8–32；不强制下线 |
| username 隔离 | ✅ | 房间成员/聊天/WS 事件只返回 nickname；历史消息查询时实时取 nickname |
| 口令存储 | ✅ | scrypt；旧明文登录时重哈希 + `scripts/migrate_passwords.py` |
| nickname 迁移 | ✅ | `scripts/migrate_f.py` 幂等补列/回填 |

## 3. 房间

| 功能 | 状态 | 说明 |
|---|---|---|
| 创建 | ✅ | 公共/私有；5 位房号 `[A-Z0-9]`；私有房密码 scrypt（4–32）；房主自动入房 |
| 公共列表 | ✅ | `GET /rooms`；只含未关闭未软删 public；cursor |
| 我的房间 | ✅ | `GET /rooms/mine`；已加入、未关闭未删除（公开/私有）；`joined_at`/`role`；过滤在 SQL 后分页 |
| 详情/加入/退出 | ✅ | `GET /rooms/{id_or_code}`、join/leave；成员上限 8；退出摘 WS 会话 |
| 房主改名/改密 | ✅ | `PATCH` 仅 name/password（1–30/4–32）；广播 `room_updated`；visibility 不可切换 |
| 踢人 | ✅ | 仅房主；定向 `room_kicked{by}`；其他成员 `user_left(kicked)`；状态/技能清理 |
| 关闭/软删 | ✅ | `DELETE` → `deleted=true`+closed_at；广播 `room_deleted`、摘全房会话；软删 code 不复用；重复删除幂等 |
| 软删可见性 | ✅ | 列表/详情/历史/画板/技能/join 一律 `ROOM_NOT_FOUND` |
| 历史窗口 | ✅ | 按成员 `joined_at`：<7 天看 7 天、≥7 天看 30 天；响应 `window_days/truncated`；窗口外游标 400 |

## 4. 聊天

| 功能 | 状态 | 说明 |
|---|---|---|
| WS 发送 | ✅ | `chat_send`；1–2000 字符；5 条/秒/用户；含自己广播；`client_msg_id` 回传 |
| 持久化 | ✅ | `room_messages` 表；不再展示的旧消息物理清理 30 天 |
| 历史 API | ✅ | `GET /rooms/{id_or_code}/messages`；倒序游标；7/30 天窗口 |
| 清理任务 | ✅ | 随 lifespan 周期执行（默认 24h，启动先跑）；30 天消息 + 7 天画板快照 |

## 5. 画板（D）

| 功能 | 状态 | 说明 |
|---|---|---|
| 增量 | ✅ | `canvas_op` stroke/clear；0–1 坐标、≤512 点、30 条/秒；只广播他人；rev 连接级 |
| 快照 | ✅ | `canvas_snapshot_save` ≤5000 笔/≤512KB；所有成员可写 LWW；`room_joined.canvas_snapshot`、`GET /canvas` 合并 DB∪内存 |
| 落盘 | ✅ | 显式 save + 关房 flush；`clear` 空板也落库 |
| 保留 | ✅ | 快照 7 天，随清理任务删除 |
| 暂缓 | ⏸ | 无自动快照（60s/30s）、无 undo、无复合游标（同微秒分页理论丢行） |

## 6. 技能共享（E1）

| 功能 | 状态 | 说明 |
|---|---|---|
| 上报/校验 | ✅ | `skill_share_update`；固定槽位 0 大招/1 技能/2 投掷物；`remaining_ms` 0–600000、`max_charges` 1–9、`pct` 0–100；非法 `INVALID_SKILL_PAYLOAD` |
| 限频 | ✅ | 2 条/秒/用户，超限 `RATE_LIMITED`（不落库不广播） |
| 服务端折算 | ✅ | `adjusted=max(0, remaining-(now-captured_at))`；`ready_at=now+adjusted`；未来不放大、>10s 归零、负值归零 |
| 广播/下发 | ✅ | `skill_share_state`（含自己）；`room_joined.skill_states`；`GET /rooms/{id}/skills`；`skill_share_off`→`skill_share_removed` |
| stale | ✅ | `updated_at` >30 分钟视为未共享，GET/room_joined 直接过滤 |
| 清理 | ✅ | 关房/软删清整房；leave/kick 无 WS 连接也 DB 删除；断线保留 |

## 7. 配置中心（E2）

| 功能 | 状态 | 说明 |
|---|---|---|
| 接口 | ✅ | `GET /api/v1/config/operators`，公开无需 JWT；IP 限频默认 60/分 |
| 数据 | ✅ | `assets/operators.json`（17 干员，含 `haizhua`/`yedan`、未知 CD 槽、完整 `_meta`） |
| 版本/缓存 | ✅ | version=etag=内容 sha256 前 16 位；`ETag`、`Cache-Control: public,max-age=60`；`If-None-Match`→304 |
| 空配置保护 | ✅ | 空数组/缺字段/结构非法→503 `CONFIG_UNAVAILABLE`，绝不下发空列表 |
| 生效方式 | ✅ | 启动加载内存；改文件后 `docker restart delta-service-container` |

## 8. WebSocket 协议

- 首消息 `auth` JWT；未认证 `NOT_AUTHENTICATED`；单用户单连接（顶号 close 4000）。
- 房间：`join_room`（需 HTTP 已加入）、`leave_room`、`user_joined/user_left`、`room_updated`、`room_deleted`、`room_kicked`。
- 聊天：`chat_send/chat_message`；画板：`canvas_op`、`canvas_snapshot_save/saved`；技能：`skill_share_update/state`、`skill_share_off/removed`。
- 心跳 30s；`ping→pong`；错误体 `{type:error,code,error_code,message}`。
- 详细字段/顺序/错误码见 [PROTOCOL.md](PROTOCOL.md) §6。

## 9. 测试与质量

| 项 | 状态 |
|---|---|
| 单元测试 | ✅ 412 个 stdlib unittest，全离线（stub FastAPI/Tortoise/PyJWT/websockets） |
| 真实 DB 集成 | 手工/QA 探针（sqlite/MySQL），不在 unittest 套件 |
| 代码扫描 | gitleaks / pre-commit 配置存在；CI 尚未自动跑单测 |
| 已知限制 | 单进程内存态（在线/画板/技能/限频）、无自动画板快照、无 refresh token、WS 画板 rev 连接级、备份未自动化 |
| 后续优化 | E3 素材补齐/灰带比例实验、E4 更多干员覆盖与精度调优、画板 undo/自动快照、复合游标、多实例共享状态、配置热加载、CI 单测 |

## 10. 部署现状

- 生产 HTTPS：`https://deltachat.icu:8443`，Caddy 静态证书（1 年，手动续期），systemd `delta-tls.service`。
- 容器 `delta-service-container`：仅 8000；`FORWARDED_ALLOW_IPS=172.17.0.1`；CI 与手动兜底均已带。
- 升级必须跑幂等 `scripts/migrate_f.py`；配置改动后重启容器。
- 详细步骤/验证/回滚：[DEPLOYMENT.md](DEPLOYMENT.md)。
