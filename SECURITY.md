# 安全说明（SECURITY）

> 适用于 `master@160c5a1`。本文记录当前安全机制、已知边界与漏洞上报方式；功能/技术债清单见 [FEATURES.md](FEATURES.md)，部署细节见 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 1. 漏洞上报

- **请勿在公开 Issue / PR / 聊天群里粘贴口令、Token、私钥或可复现的利用细节。**
- 推荐通过 GitHub 仓库的 **Security → Advisories → Report a vulnerability** 私密上报（可附影响版本/提交号、复现步骤、影响面）。
- 维护者确认后会评估影响并与报告者约定修复/披露时间；修复发布后可在 Advisory 中公开致谢。
- 如果不确定问题是否属于安全范畴（例如信息泄露、越权、DoS），也建议走私密渠道，由维护者判断。

## 2. 口令安全

### 2.1 算法与参数

实现位于 `security/passwords.py`，仅使用 Python 标准库：

| 项 | 值 |
|---|---|
| 算法 | `hashlib.scrypt` |
| 参数 | `N=2**14`（16384）、`r=8`、`p=1`、`dklen=32` |
| salt | 16 字节 `os.urandom`，每次哈希随机 |
| 存储格式 | `scrypt$N$r$p$<salt_b64>$<hash_b64>`（当前 87 字符） |
| 比较 | `hmac.compare_digest`（哈希与旧明文两条路径都使用常量时间比较） |

安全细节：

- `is_hashed()` 会校验格式、N 为 2 的幂、`N ≤ 2**20`、`r ≤ 32`、`p ≤ 16`，防止被写入畸形参数导致内存放大；
- 形如 `scrypt$...` 但无法解析的值一律拒绝，**不会退化成明文比较**；
- 客户端的 `password` 不会出现在 API 响应中：`dao/user.py` 的 `User_Pydantic` 显式 `exclude=("password",)`，`api/user.py` 还有 `_PUBLIC_USER_FIELDS` 白名单二次兜底。

### 2.2 数据库字段

`users.password` 模型长度 255（`dao/user.py:12`）。当前哈希长度 87 字符，历史上 `varchar(100)` 的列可直接容纳；**若未来调大 scrypt 参数导致哈希变长，需要先确认/调整列宽**（Tortoise `generate_schemas()` 不会自动 ALTER 既有表）。

### 2.3 旧明文兼容与登录即重哈希

- `verify_password(raw, stored)` 对非哈希值按旧明文处理，使用 `hmac.compare_digest` 比较，保证迁移前用户仍能登录；
- 登录成功后，若 `is_hashed(user.password)` 为假，立即 `hash_password` 并写回（`service/user.py:267-282`）；写回失败只记服务端日志，不阻断登录；
- 创建用户、更新口令都会先哈希再入库（`service/user.py:33,145`）。

### 2.4 存量迁移脚本

`scripts/migrate_passwords.py` 把 `users` 表内所有未哈希口令就地替换为哈希；包含软删除用户；`None`/空口令跳过；**幂等**（已哈希行不再处理）；支持 `--dry-run`；不打印任何口令或哈希。

```bash
# 演练（只统计，不写库）
DB_HOST=... DB_PORT=3306 DB_USER=delta_app DB_PASSWORD='***' DB_NAME=delta_service \
  python scripts/migrate_passwords.py --dry-run

# 正式迁移（可重复执行）
python scripts/migrate_passwords.py
```

执行建议：

1. 先备份 `users` 表；
2. 先 `--dry-run` 确认待迁移行数；
3. 使用对 `users` 表有 `UPDATE` 权限的最小权限账号（不要用 root）；
4. 迁移后旧明文只可能残留在**历史备份/历史日志**中，需按数据保留策略清理。

### 2.5 迁移后的回滚限制

口令哈希上线后，**不能回滚到哈希化之前的代码**：旧代码使用明文等值查询，无法匹配哈希 → 全体登录失败。必须回滚到包含 `security/passwords.py` 的版本或 forward-fix。详见 [DEPLOYMENT.md §5.3](DEPLOYMENT.md)。

## 3. 日志脱敏

规则位于 `log/logger.py`：

- **敏感标记**（大小写不敏感）：`password`、`passwd`、`pwd`、`token`、`secret`、`credential`、`api_key`、`apikey`；
- SQL 文本命中标记时，**绑定参数整体替换**为 `***REDACTED***`（不记录任何参数值，宁可过度打码）；
- SQL 执行失败时的 `error` 字段命中标记时，同样替换为 `***REDACTED***`，未命中则保留（排障需要）；
- 所有 SQL 日志路径（`log/sql_logger.py` 的中间件与兜底方法重写）在写日志前统一调用同一函数；
- API 请求日志**不记录请求体/响应体**，登录口令不会进入 API 日志；
- 全局未捕获异常只向客户端返回 `{code,message,request_id}`，异常类型/消息/traceback 仅写服务端日志；
- 房间/用户服务层对外文案已做白名单/通用文案收敛，不再把 `str(e)` 回传给客户端；
- **已知残留**：`websocket/websocket_manager.py:126` 会记录收到的 WS 原始消息（可能包含聊天内容），属于隐私面风险，见 FEATURES 技术债。

## 4. 仓库与供应链防护

| 措施 | 位置/状态 |
|---|---|
| Secret Scanning | 需在 GitHub 仓库 Settings → Code security and analysis 开启（公开仓库免费） |
| Push Protection | 同上开启；可在提交阶段直接拦截已知 provider 密钥 |
| Custom patterns | 建议增加“数据库口令赋值”“硬编码公网 DB 地址”等自定义规则 |
| gitleaks 配置 | `.gitleaks.toml`（内置规则 + 4 条自定义规则 + 占位符白名单） |
| pre-commit | `.pre-commit-config.yaml`：gitleaks、detect-private-key、大文件/冲突检查；开发者需 `pip install pre-commit && pre-commit install` |
| `.gitignore` | 忽略 `logs/`、`.idea/`、`.env*`（保留 `.env.example`）、私钥/证书、Python 缓存等 |
| `.dockerignore` | 排除 `.git`、`.env*`、`logs/`、IDE 配置等，避免凭据/日志进镜像层 |
| CI 凭据 | 数据库口令走部署机 `/etc/delta-service/db.env`（root:runneruser 0640），不进 GitHub Secrets、不进日志、不在命令行出现 |
| Action 供应链 | `actions/checkout` 固定到 commit SHA；`permissions: contents: read`；checkout 不持久化 token |

建议（尚未落地）：

- 在 CI 增加 `gitleaks detect` 与依赖漏洞扫描（如 `pip-audit`）；
- 定期轮换部署机数据库账号口令，并更新 `db.env`；
- 给生产 runner 打独立标签、限制可触发部署的分支/环境审批。

## 5. 历史清理状态

背景：仓库公开历史中曾出现过明文数据库口令（`config/database.py`、历史 `docker-compose.yml`、历史 CI workflow）。处置记录：

| 项 | 状态 |
|---|---|
| 暴露的数据库口令 | 已按事件处置记录在数据库侧轮换；本仓库当前代码中不再包含该口令 |
| git 历史重写 | 已使用 `git filter-repo` 重写历史，移除明文口令（按处置记录同时清理了 `.idea/`、旧日志、历史 `docker-compose.yml` 等） |
| 旧提交 SHA | **可能仍被 GitHub 缓存/被 fork/被第三方镜像收录**；不能假设重写后旧内容完全不可达 |
| 强制推送 | 已执行；所有协作者需重新克隆，避免把旧提交推回 |
| Actions 历史日志 | 按事件处置记录清理；GitHub 侧审计副本以平台为准，如仍能看到旧明文请立即上报 |
| 当前状态 | 本次代码通读未发现明文凭据；建议合并后**重新跑一次** `gitleaks detect --log-opts="--all"` 做最终确认 |

注意事项：

- 历史重写只是“止损”，**轮换才是根本**；任何曾暴露过的口令都应视为已泄露；
- 如仍能通过旧 SHA/缓存/搜索引擎看到旧敏感内容，请立即按第 1 节私密上报；
- fork 不受上游重写影响，必要时联系 fork 所有者删除。

## 6. 已知安全边界（详见 FEATURES）

以下不是“配置问题”，而是当前实现的边界，部署到公网前必须知悉：

- **HTTP API 无鉴权**：任何人可创建/查询/更新/软删除用户、创建/加入/离开房间；
- **WebSocket 鉴权是弱校验**：只检查用户名是否在“已登录在线表”中，不校验口令/token，可冒名在线用户；
- **CORS**：`allow_origins=["*"]` + `allow_credentials=True`（浏览器规范下该组合会被拒绝/不安全）；
- **无速率限制/无请求体大小限制/无 TLS**（建议在反向代理层补）；
- **WS 只绑定 localhost**：容器端口映射对外不可用，也意味着当前部署形态下外网 WS 实际不可达；
- **房间/在线状态仅在内存**：重启丢失、无法多实例，故当前也不具备横向扩展下的会话一致性。

详细条目与代码依据见 [FEATURES.md](FEATURES.md) 的「已知限制/技术债」。

## 7. 部署安全要点

- 数据库使用最小权限应用账号（不要 root），`3306` 不对公网开放；
- `/etc/delta-service/db.env` 权限 `root:runneruser 0640`，不进仓库/镜像；
- 生产 runner 与其它环境隔离，避免测试仓库/PR 使用生产凭据；
- 升级 runner 必须校验 SHA256（见 DEPLOYMENT §2.4）；
- 旧文档 `GITHUB_ACTIONS_SETUP.md` 已删除：它建议 `cat ~/.ssh/id_rsa` 并粘贴到 Secrets，容易把服务器主私钥外带；如仍需 SSH 部署，应使用专用、受限的 deploy key。
