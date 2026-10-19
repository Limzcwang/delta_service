# Delta Service 后端 ↔ 桌面客户端协议（PROTOCOL）

> **状态**：里程碑 A 契约基线（2026-10）。
> **适用范围**：仅服务 **桌面客户端**（不做 Web 客户端）。后续实现以本文为唯一依据；与代码冲突时以本文目标为准，并在实现 PR 中同步修订本文。
>
> **里程碑**：A（稳定性修复 + 协议契约，当前）→ B（JWT 认证 + 房间模型）→ C（聊天）→ D（画板）→ E（技能共享）。
> 本文描述的是 **A 交付时的现状 + B~E 的目标契约**；每个字段/接口都标注了落地阶段。标记 `【现状】` 的是当前 HEAD 已存在的行为，`【B】/【C】/【D】/【E】` 是计划行为（当前代码尚未实现）。

---

## 0. 术语与产品边界

| 词 | 含义 |
|---|---|
| 桌面客户端 | Windows/macOS 桌面应用；本地完成屏幕采集、技能识别、画板渲染 |
| 房间 | 一次协作会话，5 位房间码，public/private |
| 画板 | 逻辑画布固定 **1920×1080**，客户端等比缩放到窗口；底图由运营方配置 |
| 技能 | 客户端本地识别出的结构化技能状态（服务端不接收图像） |
| 房主 | 创建房间的用户，拥有房间管理权限 |

明确不做：Web 客户端、浏览器端渲染、用户上传底图、服务端屏幕采集/图像识别、服务端保存原始屏幕画面。

---

## 1. 通用约定

### 1.1 Base URL 与传输

| 项 | 现状（A） | 目标（B 起） |
|---|---|---|
| HTTP Base | `http://<host>:8000` | `https://<host>`（TLS 由反向代理终结） |
| HTTP 路径前缀 | `/api`（无版本） | `/api/v1`（旧 `/api` 过渡期保留，下线时间见开放问题） |
| WebSocket URL | **`ws://<host>:8000/ws`**（B2b 起与 HTTP 同端口，FastAPI `/ws`） | `wss://<host>/ws`（反代只需代理 8000 一个上游） |
| WebSocket 监听 | 与 HTTP 共用 uvicorn 8000；旧独立 8765 仅 `LEGACY_WS_ENABLED=true` 时启动（默认 false） | 同左 |

> **安全提醒**：B2b 起 WebSocket 与 HTTP 共用 8000（`/ws`），业务只需开放 8000 一个端口并在生产用反向代理终结 TLS；旧 8765 server 默认关闭，不应再对公网开放。

### 1.2 时间与编码

- 时间：**Unix 毫秒时间戳**（`int64`），字段名以 `_at` / `ts` 结尾；服务端另有 ISO8601 字符串仅用于日志，不进入协议。
- 字符集：UTF-8；JSON 使用 `application/json`，禁止 BOM。
- 坐标：画板逻辑像素 `[0,1920] × [0,1080]`，原点左上角；客户端按窗口等比缩放后换算回逻辑坐标再上报。
- ID：用户 `user_id` 在协议中一律为 **字符串**（数据库自增 ID 序列化为字符串）；房间 `room_id` 为服务端生成的字符串（建议 ULID/UUID）。

### 1.3 请求头

| Header | 必填 | 说明 |
|---|---|---|
| `Content-Type` | POST/PUT | `application/json` |
| `Authorization` | B 起全部业务接口 | `Bearer <JWT>` |
| `X-Protocol-Version` | 建议 | 客户端协议版本，整数，A=1 |
| `X-Request-Id` | 可选（B 起） | 客户端生成的请求追踪 ID（≤64 字符），服务端回显到响应 `request_id` 并落日志；A 现状由服务端自行生成 |

### 1.4 统一响应包（目标格式）

**目标（B 起新接口）**：

```json
{
  "code": 200,
  "message": "OK",
  "data": {},
  "request_id": "req_ab12cd34ef56"
}
```

- `code`：通用数字业务码，成功固定 `200`；错误为 `4xxxx/5xxxx`（与 HTTP 状态码分开，见 §7.1）。
- `data.error_code`：**字符串业务错误码**（如 `ROOM_NOT_FOUND`/`NOT_ROOM_MEMBER`/`INVALID_PAGINATION`），客户端的精确分支以它为准；成功响应 `data` 为业务数据，不携带该字段。
- `message`：面向用户/开发者的简短文案，**不含异常堆栈、SQL、连接串**。
- `data`：成功时的业务数据；失败时通常为 `{"error_code": "..."}`（无具体业务码的历史接口可能为 `null`）。
- `request_id`：字符串，可选；失败响应必须尽量携带，便于与服务端日志对账。

**过渡状态（A，必须知道）**：

| 来源 | 当前形态 |
|---|---|
| 房间接口 | 裸 JSON（`{"room_code":"..."}`），错误为 `HTTPException.detail` → 线上被 FastAPI 包一层：`{"detail":{"error_code":"ROOM_NOT_FOUND","message":"..."}}` |
| 用户接口 | `{"code":200,"message":...,"data":...}`；错误为 `{"detail":"字符串"}` |
| 全局未捕获异常 | `{"code":500,"message":"服务器内部错误","request_id":"req_..."}` |
| FastAPI 参数校验 | HTTP 422，`{"detail":[{"type","loc","msg","input"}]}` |

B 阶段：新增接口一律使用目标格式；旧接口在过渡期保留原格式，由客户端适配层兼容；C 阶段结束前统一（见 §10 版本策略）。

### 1.5 分页

- 消息历史：请求 `?limit=<1..100，默认 50>&before=<上一页最后一条消息 id>`；`before` 是**消息 id 字符串**（不是时间戳）。
- 消息历史响应：`data: {"items": [...], "next_before": <消息 id 字符串|null>, "has_more": true|false}`；用户列表沿用 `{"users":[],"total":N}`。
- 消息历史排序：按 `created_at` **倒序（新→旧）**；消息/房间响应中的 `created_at` 为 Unix 毫秒整数。
- 用户列表分页沿用 `?skip=<int>&limit=<int>`。

---

## 2. 认证（JWT，B 阶段落地）

### 2.1 方案

| 项 | 约定 |
|---|---|
| 算法 | **HS256**（`PyJWT`），服务端密钥 `JWT_SECRET`（≥32 字节随机，只放部署机 env） |
| Header | `Authorization: Bearer <token>`（HTTP）；WebSocket 首消息携带（§6.2） |
| Claims | `sub`=用户 ID 字符串、`iat`、`exp`、`jti`（可选）、`ver`=协议版本（建议） |
| 有效期 | **7 天**：`exp = iat + 7d`（`JWT_EXPIRE_DAYS` 默认 7，`expires_in=604800`）。**无 refresh token**，过期后客户端重新登录；紧急失效通过轮换 `JWT_SECRET` 让全部已签发令牌立即失效 |
| 时钟偏差 | 服务端允许 **±60s**（`decode_access_token` 的 `leeway=60`，容忍桌面端时钟漂移） |
| 错误 | 缺失/格式错/过期/签名错分别返回 401 + 错误码（§7）；**不区分“用户不存在”和“密码错误”** |

> `JWT_SECRET` 长度 <32 字节时，服务端首次使用会 `logger.warning` 提醒但**不阻断**（方便本地开发）；生产环境务必使用 ≥32 字节随机密钥。

> 当前 HEAD（A）**没有 JWT**：`POST /api/users/login` 只校验口令并写入进程内在线表，不返回 token；WebSocket 的 `authenticate` 只校验用户名是否在线。B 阶段替换为 JWT，并保留一个版本的兼容开关（`LEGACY_WS_AUTH=1`，默认关闭）。

### 2.2 登录（B）

`POST /api/v1/auth/login`

```json
{"username": "alice", "password": "Pa55w0rd!"}
```

成功：

```json
{
  "code": 200,
  "message": "OK",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 604800,
    "user": {"id": "1", "username": "alice"}
  },
  "request_id": "req_5f2..."
}
```

失败：`401 / 40100`（统一 `"用户名或密码错误"`，不泄露用户是否存在）。

### 2.3 令牌使用

- 所有 `/api/v1/**` 业务接口除 `auth/login` 外都要求有效 token；
- WebSocket 连接后**第一条业务消息必须是 `auth`**（§6.2），否则除 `ping` 外的消息一律回 `AUTH_REQUIRED`；
- 令牌过期：HTTP 返回 `401 + 40101`；WS 返回 `auth_error/TOKEN_EXPIRED` 后可关闭连接（关闭码建议 4001）。

---

## 3. HTTP 接口清单

### 3.1 现有 12 个接口（A 现状，过渡期保留）

> 路径为 A 现状（无 `/v1`）；B 起建议按 §3.2 提供 v1 版本，旧接口过渡期保留。错误体见 §1.4 过渡状态。

| # | 方法 | 路径 | 认证 | 参数 | 成功响应（房间为裸 body，用户为 `data`） | 主要错误 |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/room/create` | 无 | `{user_id, domain_enum?}`（1 公共/2 私有） | `{room_code, message}` | 400 `INVALID_DOMAIN`；500 `CREATE_ROOM_FAILED`/`ROOM_CODE_GENERATION_FAILED`/`INTERNAL_ERROR` |
| 2 | POST | `/api/room/join` | 无 | `{user_id, room_code}` | `{message, room_info:{room_code,domain,creator,members_count,online_count,created_at}}` | 404 `ROOM_NOT_FOUND`；500 `JOIN_ROOM_FAILED` |
| 3 | POST | `/api/room/leave` | 无 | `{user_id, room_code}` | `{message}` | 404 `ROOM_NOT_FOUND`；400 `USER_NOT_IN_ROOM`；500 `LEAVE_ROOM_FAILED` |
| 4 | GET | `/api/room/info/{room_code}` | 无 | path `room_code` | `{room_code,domain,creator,members[],online[],created_at}` | 404 `ROOM_NOT_FOUND` |
| 5 | GET | `/api/room/public/list` | 无 | – | `{rooms:[RoomInfo]}` | 500 `GET_PUBLIC_ROOMS_FAILED`/`INTERNAL_ERROR` |
| 6 | POST | `/api/users/create` | 无 | `{username, password?}` | `{code:200,message,data:{id,username}}` | 400（校验/`创建用户失败`） |
| 7 | GET | `/api/users/{user_id}` | 无 | path int | `{code,message,data:{id,username,created_at,updated_at}}` | 404 / 400 |
| 8 | GET | `/api/users/username/{username}` | 无 | path | 同 #7 | 404 / 400 |
| 9 | PUT | `/api/users/{user_id}` | 无 | `{username?,password?}` | `{code,message,data:{id,username}}` | 400 |
| 10 | DELETE | `/api/users/{user_id}` | 无 | path int | `{code,message}`（软删除） | 400 |
| 11 | POST | `/api/users/login` | 无 | `{username,password}` | `{code,message,data:{id,username}}`（**A 无 token**；B 走 §2.2） | 401 |
| 12 | GET | `/api/users/list?skip=&limit=` | 无 | query | `{code,message,data:{users:[],total}}` | 500 |

> 注：A 修复后 `GET /api/users/list` 已不再被 `/{user_id}` 遮蔽（路由顺序已调整）；`/{user_id}` 传非数字仍是 422。

### 3.2 B 阶段新增/升级接口（目标契约）

统一前缀 `/api/v1`，除登录外均需 `Authorization: Bearer`。

| 方法 | 路径 | 说明 | 请求 | 响应 `data` |
|---|---|---|---|---|
| POST | `/api/v1/auth/login` | 登录签发 JWT | `{username,password}` | `{access_token,token_type,expires_in,user}` |
| GET | `/api/v1/users/me` | 当前用户 | – | `{id,username,created_at}` |
| POST | `/api/v1/rooms` | 创建房间（房主=当前用户） | `{name, visibility:"public"\|"private", password?, background_id?}` | `{room}` |
| GET | `/api/v1/rooms` | 公共房间列表（分页） | `?limit&before` | `{items:[RoomSummary],has_more,next_before}` |
| GET | `/api/v1/rooms/{room_id_or_code}` | 房间详情（成员/在线/房主/底图） | – | `{room,members:[UserSummary]}` |
| PATCH | `/api/v1/rooms/{room_id_or_code}` | 房主改名称/底图/密码 | `{name?,background_id?,password? (null=清除)}` | `{room}` |
| DELETE | `/api/v1/rooms/{room_id_or_code}` | 房主关闭房间 | – | `{closed:true}` |
| POST | `/api/v1/rooms/{room_id_or_code}/join` | 加入房间 | `{password?}` | `{room,canvas_seq,background,skill_state}` |
| POST | `/api/v1/rooms/{room_id_or_code}/leave` | 离开房间 | – | `{left:true}` |
| GET | `/api/v1/rooms/{room_id_or_code}/messages` | 聊天历史（C） | `?before&limit` | `{items:[ChatMessage],has_more,next_before}` |
| GET | `/api/v1/rooms/{room_id_or_code}/canvas` | 画板快照（D） | – | `{seq,canvas_size:[1920,1080],background,ops:[CanvasOp]}` |
| GET | `/api/v1/rooms/{room_id_or_code}/skills` | 房间技能状态（E） | – | `{items:[{user_id,ts,skills}]}` |

`RoomSummary`（公共列表字段，产品要求）：

```json
{
  "id": "01J8Z9K3Q7",
  "code": "A1B2C",
  "name": "开黑房",
  "owner_id": "1",
  "member_count": 3,
  "has_password": true,
  "created_at": 1750000000000,
  "background_id": "bg_default_01"
}
```

`Room` = `RoomSummary` + `members:[{id,username,online}]` + `background:{id,url,width,height}`。

错误：401/403/404/409/429/5xx，见 §7。

---

## 4. 房间语义

| 项 | 约定 |
|---|---|
| 房间码 | `^[A-Z0-9]{5}$`，服务端生成并保证活跃房间内唯一；客户端输入统一转大写 |
| 可见性 | `public`（出现在公共列表）/ `private`（不出现，需密码加入） |
| 私有房间密码 | 服务端用 `security/passwords.py` 的 **scrypt** 哈希存储；**绝不返回哈希**；响应只给 `has_password: bool`；创建/修改密码由房主操作（B） |
| 房间名 | 1–32 字符（建议），房主可改 |
| 成员上限 | 建议 8 人（开放问题） |
| 加入 | 按 `room_code` 或 `room_id`；private 且 `has_password` 必须带 `password`；已在房间内视为幂等成功 |
| 离开 | 房主离开需先关闭房间或转让（开放问题） |
| 房主权限 | 改名称、选定运营底图、设置/修改/清除密码、踢人、关闭房间、清空画板（清空权限开放问题） |
| 公共列表 | 字段见 RoomSummary；仅返回未关闭的 public 房间；支持分页 |
| 生命周期 | B 起房间元数据落 MySQL（建议）；当前 A 的 `ROOM_MANAGER` 仍是进程内存、重启丢失 |

---

## 5. 画板（里程碑 D）

| 项 | 约定 |
|---|---|
| 逻辑画布 | **固定 1920×1080**；客户端等比缩放显示，上报前换算回逻辑坐标 |
| 底图 | **由运营方配置**（服务端下发的预置图），用户**不得上传**；协议字段 `background:{id,url,width,height}`；无法加载时客户端用纯色背景兜底 |
| 操作类型 | `draw`（笔画）、`undo`（撤销）、`clear`（清空） |
| 坐标 | 浮点逻辑像素，保留 1 位小数；`points:[[x,y],...]`，单 op ≤ 1024 点 |
| 顺序 | 服务端为每个房间维护单调递增 `seq`，客户端必须按 `seq` 应用；发现跳号时请求/等待 `canvas_snapshot` 重同步 |
| 快照 | 加入房间时服务端下发 `canvas_snapshot`（含当前 `seq`、背景、`ops` 列表）；新客户端不重放历史消息，只重放画板 |
| 持久化 | 建议服务端保存 op log（上限与是否落库见开放问题）；`clear` 之后历史 op 可丢弃 |

`CanvasOp`：

```json
{
  "op_id": "op_01J8Z...",
  "seq": 42,
  "user_id": "1",
  "op": "draw",
  "payload": {
    "tool": "pen",
    "color": "#FF3B30",
    "width": 4,
    "points": [[120.5, 88.0], [121.0, 89.5]]
  },
  "ts": 1750000000123
}
```

- `undo`：撤销**该用户最近一条**尚未撤销的 draw（服务端口径见开放问题；客户端 UI 只允许撤销自己的笔画）。
- `clear`：清空整块画板（建议仅房主可发）。
- 限频：单人 canvas_op 建议 ≤ 30 次/秒（批量笔画请合并 points），超限 `RATE_LIMITED`。

---

## 6. WebSocket 协议

### 6.1 连接

- URL：`ws://<host>:8000/ws`（B2b 现状，与 HTTP 同端口）；生产目标 `wss://<host>/ws`（反代同一 8000 上游）。旧独立 `ws://<host>:8765` 仅在 `LEGACY_WS_ENABLED=true` 时存在，默认关闭。
- 所有消息为 JSON 文本，单条 ≤ 64KB（超限 `INVALID_PAYLOAD` 并断开）。
- 心跳：服务端每 **30s** 下发 `heartbeat`，客户端 90s 未收到心跳可重连；客户端也可主动 `ping`。
  （B2b 现状即按此实现（30s）；旧 manager 的 data_pusher/离线检测只在 `LEGACY_WS_ENABLED=true` 时启动，`/ws` 客户端不会收到 `push` 演示数据。）
- 客户端应实现指数退避重连（1s/2s/4s…上限 30s），重连后重新 `auth` 并重新 `join_room`。

### 6.2 首消息认证（B）

客户端 → 服务端：

```json
{
  "type": "auth",
  "token": "<JWT>",
  "protocol_version": 1,
  "client": {"name": "delta-desktop", "version": "1.0.0", "os": "windows"}
}
```

成功：

```json
{
  "type": "auth_ok",
  "user": {"id": "1", "username": "alice"},
  "server_time": 1750000000000,
  "heartbeat_interval_ms": 15000
}
```

失败：

```json
{"type": "auth_error", "code": "AUTH_EXPIRED", "error_code": "AUTH_EXPIRED", "message": "登录已过期，请重新登录"}
```

> A 现状的 `{"type":"authenticate","username":...}`（仅校验在线状态）**已在 B2b 移除**：收到该 type 会返回 `auth_error`（`AUTH_INVALID`，提示认证协议已升级）并关闭连接（close code 4001）；客户端必须改用 `auth + token`。

### 6.3 客户端 → 服务端

| type | 说明 | 是否需认证 | 示例 |
|---|---|---|---|
| `join_room` | 加入房间 | 是 | 见下 |
| `leave_room` | 离开房间 | 是 | `{"type":"leave_room"}`（B2b 忽略房间参数，退出当前频道） |
| `chat_send` | 发聊天消息（C） | 是 | 见下 |
| `canvas_op` | 画板操作（D） | 是 | 见下 |
| `skill_report` | 上报技能快照（E） | 是 | 见下 |
| `ping` | 心跳 | 否 | `{"type":"ping","ts":1750000000000}` |

```json
{"type":"join_room","room_id_or_code":"A1B2C","request_id":"c-123"}
```

> `room_id_or_code` 同时接受 5 位 room_code 与内部 id，兼容旧字段 `room_code`。**前置条件：用户必须已通过 HTTP `POST /rooms/{room_id_or_code}/join` 成为成员**，否则返回 `NOT_ROOM_MEMBER`；`password` 字段忽略（HTTP 已校验），房间不存在/已关闭返回 `ROOM_NOT_FOUND`/`ROOM_CLOSED`。
```json
{"type":"chat_send","client_msg_id":"m-001","content":"大家好","content_type":"text"}
```
```json
{"type":"canvas_op","op_id":"op_001","op":"draw","payload":{"tool":"pen","color":"#FF3B30","width":4,"points":[[120.5,88.0],[121.0,89.5]]}}
```
```json
{"type":"skill_report","ts":1750000000123,"skills":[{"id":"skill-a","name":"闪现","ready_at":1750000008000,"total":1200}]}
```

### 6.4 服务端 → 客户端

| type | 说明 |
|---|---|
| `auth_ok` / `auth_error` | 认证结果（§6.2） |
| `room_joined` | 加入成功，携带房间、成员、画板快照、技能状态 |
| `user_joined` / `user_left` | 房间成员变化 |
| `chat_message` | 聊天消息（C） |
| `canvas_snapshot` / `canvas_op` | 画板快照 / 增量操作（D） |
| `skill_state` | 某用户最新技能快照（E） |
| `heartbeat` | 心跳 |
| `pong` | 对 ping 的响应 |
| `error` | 错误（§7.2） |

`room_joined`：

```json
{
  "type": "room_joined",
  "room": {
    "id": "01J8Z9K3Q7",
    "code": "A1B2C",
    "name": "开黑房",
    "owner_id": "1",
    "has_password": true,
    "member_count": 2,
    "created_at": 1750000000000,
    "background": {"id": "bg_default_01", "url": "https://cdn.example.com/bg/01.png", "width": 1920, "height": 1080}
  },
  "members": [{"id": "1", "username": "alice", "online": true}],
  "canvas_snapshot": {"seq": 42, "canvas_size": [1920, 1080], "background": {"id": "bg_default_01", "url": "https://cdn.example.com/bg/01.png", "width": 1920, "height": 1080}, "ops": []},
  "skill_state": {"items": [{"user_id": "1", "ts": 1750000000123, "skills": []}]},
  "server_time": 1750000000000
}
```

`user_joined` / `user_left`：

```json
{"type":"user_joined","room_id":"01J8Z9K3Q7","user":{"id":"2","username":"bob"},"ts":1750000001000}
{"type":"user_left","room_id":"01J8Z9K3Q7","user":{"id":"2","username":"bob"},"reason":"left","ts":1750000002000}
```

`user_left.reason` 语义（C 起）：
- `disconnected`：WS 连接断开（自动清理）；
- `left`：WS `leave_room` 或 HTTP `leave` 成功（HTTP 成功后服务端主动摘除该用户 WS 会话）；
- `kicked`：被房主通过 HTTP `kick` 踢出（同样主动摘除 WS 会话）；
- `replaced`：同账号新连接顶号，旧连接退出。

`chat_message`（C）：

```json
{
  "type": "chat_message",
  "id": "9f2c...",
  "room_id": "01J8Z9K3Q7",
  "user": {"id": "1", "username": "alice"},
  "content": "大家好",
  "client_msg_id": "m-001",
  "created_at": 1750000001500
}
```

> C1/B2b 客户端契约：字段固定为 `id` / `room_id` / `user` / `content` / `client_msg_id` / `created_at`；发送者本人也会收到广播，靠 `client_msg_id` 去重。`content_type` 暂不落库。

`canvas_snapshot` / `canvas_op`（D）：

```json
{"type":"canvas_snapshot","room_id":"01J8Z9K3Q7","seq":42,"canvas_size":[1920,1080],"background":{"id":"bg_default_01","url":"https://cdn.example.com/bg/01.png","width":1920,"height":1080},"ops":[]}
```
```json
{"type":"canvas_op","room_id":"01J8Z9K3Q7","seq":43,"op_id":"op_001","user_id":"1","op":"draw","payload":{"tool":"pen","color":"#FF3B30","width":4,"points":[[120.5,88.0]]},"ts":1750000001600}
```

`skill_state`（E）：

```json
{"type":"skill_state","room_id":"01J8Z9K3Q7","user_id":"1","ts":1750000000123,"skills":[{"id":"skill-a","name":"闪现","ready_at":1750000008000,"total":1200}]}
```

`heartbeat` / `pong` / `error`：

```json
{"type":"heartbeat","server_time":1750000015000}
{"type":"pong","ts":1750000000000}
{"type":"error","code":"ROOM_PASSWORD_INVALID","message":"房间密码错误","request_id":"req_..."}
```

### 6.5 兼容与容错

- 客户端必须**忽略未知字段**与未知 `type`（不要因新增消息而崩溃）；服务端对未知 `type` 回 `{"type":"error","code":"INVALID_PAYLOAD"}` 或忽略（实现二选一，需一致）。
- `client_msg_id` / `op_id` 用于客户端去重与重试，服务端原样回传。
- 重连后状态以 `room_joined` 的 `canvas_snapshot` + `skill_state` 为准，不依赖增量补齐。

---

### 6.6 B2b 当前实现状态（客户端必读）

B2b 已落地 JWT 认证与房间频道，以下为**当前实际行为**，与上文目标契约的差异会在 C/D/E 补齐：

| 项 | 当前实现 |
|---|---|
| 连接路径 | `ws://<host>:8000/ws`（FastAPI 路由，与 HTTP 同端口）；旧 8765 默认关闭 |
| 首消息 | 必须是 `auth`；`auth_ok` 含 `user`、`server_time`、`heartbeat_interval_ms`；失败 `auth_error` 后关闭（code 4001） |
| 旧 `authenticate` | **已移除**：返回 `auth_error`（`AUTH_INVALID`）并关闭 |
| 未认证消息 | `error`，`code=NOT_AUTHENTICATED`，连接保持（可继续尝试 auth） |
| 单用户单连接 | 新连接认证成功时关闭旧连接（close code 4000），旧连接退出房间并广播 `user_left(reason=replaced)` |
| 已支持消息 | `auth`、`join_room`、`leave_room`、`chat_send`、`ping`；`canvas_op`/`skill_report`/旧 `room_message` 等返回 `UNSUPPORTED_MESSAGE_TYPE`（D/E 实现） |
| chat_send | 需已进房；限频 5 条/秒/用户（超限 `RATE_LIMITED`）；空/超长（>2000）`INVALID_MESSAGE`；非成员 `NOT_ROOM_MEMBER`；成功后广播 `chat_message` 给房间内所有连接（含发送者），`client_msg_id` 原样回传 |
| 历史消息 | `GET /api/v1/rooms/{room_id_or_code}/messages?before=&limit=`；`before` 为上一页最后一条消息 id（字符串），`limit` 默认 50/上限 100；需 JWT + 房间成员 |
| HTTP leave/kick | 成功后服务端摘除该用户在当前房间的 WS 会话（不再收 `chat_message`，在线状态消失），并向余下成员广播 `user_left(reason=left\|kicked)` |
| join_room | 要求已认证 + 已是房间成员（HTTP join 过）；非成员 `NOT_ROOM_MEMBER`；房间不存在 `ROOM_NOT_FOUND`；已关闭 `ROOM_CLOSED`；成功回 `room_joined{room,members}`（members 含 `online`） |
| 离开/断线 | `leave_room` 回 `left_room` 并向房间其他成员广播 `user_left(reason=left)`；断开自动清理并广播 `user_left(reason=disconnected)` |
| 在线状态 | 由“当前活跃 WS 连接且在该房间”推导，不读数据库；不跨进程 |
| 心跳 | 服务端每 30s 下发 `heartbeat`，客户端 90s 未收到可重连；保留 `ping→pong` |
| 错误格式 | `{"type":"error","code":"...","error_code":"...","message":"..."}`：`code` 为新协议字段，`error_code` 为旧字段兼容（同值） |
| 房间事件 | `user_joined` / `user_left` 载荷：`room_id`、`room_code`、`user{id,username}`、`ts`（ms）、可选 `reason` |
| 画板/技能 | 当前 `room_joined` **不包含** `canvas_snapshot` / `skill_state`（D/E 阶段加入） |

## 7. 错误码与状态码

### 7.1 HTTP 错误码（目标）

| code | HTTP | 含义 |
|---|---|---|
| 200 | 200 | 成功 |
| 40000 | 400 | 参数/请求体校验失败 |
| 40100 | 401 | 未认证（缺少或格式错误的 token） |
| 40101 | 401 | token 过期 |
| 40102 | 401 | token 无效（签名错/用户不存在） |
| 40300 | 403 | 无权限（非房主、被封禁等） |
| 40400 | 404 | 资源不存在 |
| 40401 | 404 | 用户不存在 |
| 40402 | 404 | 房间不存在 |
| 40900 | 409 | 资源冲突（用户名已存在等） |
| 40901 | 409 | 房间号冲突/房间已关闭 |
| 40902 | 409 | 已在房间中（可选，幂等实现可返回 200） |
| 42200 | 422 | 框架级参数校验失败（Pydantic） |
| 42900 | 429 | 触发限频 |
| 50000 | 500 | 服务器内部错误 |
| 50001 | 500 | 数据库/依赖错误 |
| 50300 | 503 | 服务暂不可用 |

**A 现状 → 目标映射**：`ROOM_NOT_FOUND`→40402、`INVALID_DOMAIN`/`USER_NOT_IN_ROOM`→40000（或 40402）、`ROOM_ALREADY_EXISTS`→40901、`CREATE_ROOM_FAILED` 等→50000、全局 `INTERNAL_ERROR`→50000；用户接口的字符串 detail 在 B/C 收敛为 `message` + `code`。

### 7.2 WebSocket 错误码

WS 的 `error.code` 使用 **UPPER_SNAKE 字符串**（与 HTTP 数字码区分），`message` 面向用户：

| code | 含义 |
|---|---|
| `NOT_AUTHENTICATED` | 未认证即发送需要认证的消息 |
| `AUTH_INVALID` | token 无效/用户不存在/认证配置错误 |
| `AUTH_EXPIRED` | token 过期 |
| `ALREADY_AUTHENTICATED` | 已认证后重复发送 `auth` |
| `NOT_ROOM_MEMBER` | 非房间成员（聊天/历史接口/未进房） |
| `NOT_ROOM_OWNER` | 非房主执行房主操作（如踢人/关闭） |
| `MEMBER_NOT_FOUND` | 目标成员不存在 |
| `ROOM_NOT_FOUND` | 房间不存在 |
| `ROOM_CLOSED` | 房间已关闭 |
| `ROOM_PASSWORD_REQUIRED` | 私有房间需要密码 |
| `ROOM_PASSWORD_INVALID` | 房间密码错误 |
| `ROOM_FULL` | 房间人数已满（8 人） |
| `RATE_LIMITED` | 触发限频（聊天：5 条/秒/用户） |
| `INVALID_MESSAGE` | 聊天内容为空/超过 2000 字符或 client_msg_id 非法 |
| `INVALID_PAYLOAD` | JSON 对象结构/字段非法 |
| `INVALID_JSON` | 消息不是合法 JSON |
| `UNSUPPORTED_MESSAGE_TYPE` | 当前版本未实现的消息类型 |
| `CANVAS_SEQ_GAP` | 画板操作序号不连续（需重同步，D 阶段） |
| `INTERNAL_ERROR` | 服务端内部错误 |

安全约定（与现有实现一致）：错误响应不含异常堆栈/SQL/连接串；服务端日志通过 `request_id`/`user_id` 对账。

---

### 7.3 HTTP `data.error_code` 映射（B2a/C 实际）

| error_code | HTTP | 顶层 code |
|---|---|---|
| `ROOM_NOT_FOUND` | 404 | 40402 |
| `MEMBER_NOT_FOUND` | 404 | 40400 |
| `NOT_ROOM_MEMBER` / `NOT_ROOM_OWNER` | 403 | 40300 |
| `ROOM_PASSWORD_REQUIRED` / `ROOM_PASSWORD_INVALID` / `ROOM_NAME_INVALID` / `INVALID_BACKGROUND` / `INVALID_VISIBILITY` / `ROOM_PASSWORD_NOT_ALLOWED` / `CANNOT_KICK_OWNER` / `INVALID_PAGINATION` / `INVALID_CURSOR` / `INVALID_MESSAGE` | 400 | 40000 |
| `ROOM_FULL` / `ROOM_OWNER_MUST_CLOSE` | 409 | 40900 |
| `ROOM_CLOSED` | 409 | 40901 |
| `RATE_LIMITED` | 429 | 42900 |
| `ROOM_CODE_GENERATION_FAILED` | 500 | 50000 |

成功响应的 `data` 为业务数据；错误响应 `data={"error_code":"..."}`，客户端按该字符串分支（如弹房间密码框）。

## 8. 技能共享（里程碑 E）

| 项 | 约定 |
|---|---|
| 数据来源 | **全部在客户端本地完成**屏幕采集与技能识别；服务端只接收结构化快照，**不接收任何图像/截图/二进制** |
| 上报频率 | 每 **1–2 秒**一次全量快照（不是增量） |
| 服务端限频 | 建议每人 **2 次/秒**（超出丢弃，必要时回 `RATE_LIMITED`；不因超频断开连接） |
| 广播范围 | 仅房间内成员；发送者自己也会收到回显（便于确认） |
| 保留策略 | 服务端按 `(room_id, user_id)` 保留**最近一份**；新成员加入时通过 `room_joined.skill_state` 或 `GET /skills` 拉取 |
| 离开房间 | 删除该房间内该用户的技能状态 |
| 载荷上限 | `skills` ≤ 64 项，单条 `name` ≤ 32 字符，整体 JSON ≤ 8KB |

载荷：

```json
{
  "type": "skill_report",
  "ts": 1750000000123,
  "skills": [
    {"id": "skill-a", "name": "闪现", "ready_at": 1750000008000, "total": 1200},
    {"id": "skill-b", "name": "传送", "ready_at": null, "total": 300}
  ]
}
```

字段说明：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | string | 技能稳定标识（客户端本地表） |
| `name` | string | 展示名（服务端原样广播，不做业务解释） |
| `ready_at` | int64/null | 预计可用时间（Unix ms）；`null` 表示未知/不可用 |
| `total` | int | 总量或累计值（具体语义由客户端定义，服务端只透传） |
| `ts` | int64 | 本次快照时间（Unix ms） |

---

## 9. 里程碑与协议演进

| 里程碑 | 内容 | 协议变更 |
|---|---|---|
| **A**（当前） | 稳定性修复（`/list` 路由、WS 绑定、后台任务去重、SQL 日志初始化幂等）+ 本契约 | 无新接口；确定目标契约 |
| **B** | JWT（HS256）+ 房间模型 | `/api/v1`、`auth/login`、房间 CRUD/密码、WS `auth/join_room` |
| **C** | 聊天 | `chat_send` / `chat_message` / 历史接口 |
| **D** | 画板 | `canvas_op` / `canvas_snapshot` / 底图字段 |
| **E** | 技能共享 | `skill_report` / `skill_state` / `GET skills` |

---

## 10. 版本与向后兼容策略

1. **协议版本**：整数 `protocol_version`，A=1；通过 HTTP `X-Protocol-Version` 与 WS `auth.protocol_version` 传递。
2. **兼容规则**：
   - 新增字段/新增消息类型 = 向后兼容，服务端与客户端都必须忽略未知字段/未知类型；
   - 删除/改名字段、改语义、改认证方式 = 破坏性变更，必须升 major 并保留旧路径至少 **2 个发布版本**；
   - 服务端在弃用期同时支持新旧字段（旧字段只读映射），并在日志/响应头 `Deprecation` 提示。
3. **HTTP 路径版本化**：目标 `/api/v1`；旧 `/api` 在 B 起标记 deprecated，C 结束前保留（具体下线时间见开放问题）。
4. **客户端最低版本**：服务端可在 `auth_ok` 返回 `min_client_version`，低于该版本的客户端拒绝连接并提示升级。
5. **数据库与协议解耦**：协议字段不直接暴露数据库列名/自增 ID 语义；ID 一律字符串化。

---

## 11. 待确认的开放问题（需要产品/用户拍板）

| # | 问题 | 建议默认值 | 影响 |
|---|---|---|---|
| Q1 | 是否启用 `/api/v1` 前缀？旧 `/api` 何时下线？ | B 起启用 v1，旧路径保留到 C 结束 | 客户端 Base URL/迁移成本 |
| Q2 | JWT 有效期与刷新策略（是否 refresh token / 单点登录） | **已定：7 天、无 refresh，过期重登（B1 实现）**；是否增加 refresh/滑动续期后续再议 | 认证实现与桌面端体验 |
| Q3 | 房间元数据是否落 MySQL、成员上限、房间名长度 | 落库、8 人、1–32 字符 | B 的 schema 与容量 |
| Q4 | 房主离开/转让/踢人/关闭房间的细则；清空画板权限 | 房主需先转让或关闭；仅房主可清空 | B/D 权限模型 |
| Q5 | 画板 undo 语义（按用户/全局）、op 保留深度、是否落库 | 仅撤销自己的笔画、保留最近 5000 ops、内存+定期快照 | D 的实现与内存占用 |
| Q6 | 聊天历史保留策略（内存/DB、条数/天数、分页游标） | MySQL 保留 30 天，游标分页 | C 的存储与合规 |
| Q7 | 技能 `ready_at`/`total` 的精确定义；技能列表是否固定 | 由客户端定义、服务端透传；列表固定 | E 的展示一致性 |
| Q8 | 限频具体阈值（HTTP 登录、WS 聊天、画板、技能） | 登录 5 次/分/IP；聊天 5 条/秒/人；画板 30 op/秒/人；技能 2 次/秒/人 | 防滥用与体验 |
| Q9 | 底图的存储/分发（CDN、URL 是否鉴权、谁选择底图） | 运营预置 + CDN 公共读；房主从预置中选 | D 的部署与版权 |
| Q10 | 单用户多端策略（同账号多 WS 连接/互踢） | 允许 1 个 WS，重连顶掉旧连接 | B 的会话管理 |
| Q11 | 桌面端平台/最低系统版本；TLS 是否统一由反代终结 | Windows 10+ / macOS 12+；统一反代 TLS | 客户端与部署 |
| Q12 | 服务端是否保存聊天/画板原始数据及保留期（合规） | 聊天 30 天、画板快照 7 天 | 存储与隐私政策 |

---

## 12. 与当前代码的差异清单（实现者必读）

| 契约项 | 当前 HEAD（A） | 目标 |
|---|---|---|
| 认证 | 无 HTTP 鉴权；WS 仅校验用户名是否在内存在线表 | JWT（B） |
| 响应包 | 房间裸 JSON/`error_code`；用户 `code/message/data`；全局 500 `code/request_id` | 统一 `{code,message,data,request_id?}`（B/C 迁移） |
| 房间 | 内存 `ROOM_MANAGER`、无名称/密码/落库；5 位码 `[A-Z0-9]` | 落库房间模型 + 私有密码（B） |
| WS 监听 | FastAPI `/ws` 与 HTTP 共用 8000；旧 8765 默认关闭（`LEGACY_WS_ENABLED=false`） | 同左 + 反代/TLS |
| WS 消息 | `authenticate/join_room/leave_room/room_message/echo/broadcast/ping/activity` | `auth/join_room/leave_room/chat_send/canvas_op/skill_report/ping` |
| 画板 | 无 | D 阶段实现 |
| 技能 | 无 | E 阶段实现 |
| 签名/限频 | 无 | B/E 逐步引入 |
