# 部署与运维（DEPLOYMENT）

> 适用于 `master@160c5a1`（含安全 PR #1/#2/#3）。本文替代已删除的 `GITHUB_ACTIONS_SETUP.md`（那是基于 SSH 的旧部署方案，已被 self-hosted runner 本机构建取代）。
>
> 文中 `/etc/delta-service/`、`/root/delta-deploy.sh`、`/root/update-runner.sh` 等路径位于**部署机**，不在本仓库内；仓库内的唯一流水线定义是 `.github/workflows/deploy.yml`。两者不一致时，以仓库流水线 + 部署机实际脚本为准，并同步修正文档。

---

## 1. CI/CD 现状

| 项 | 值 |
|---|---|
| 触发 | push 到 `master`；或手工 `workflow_dispatch` |
| Runner | **self-hosted**（部署机本机），`environment: production`，`runs-on: self-hosted` |
| 权限 | `permissions: contents: read`；checkout `persist-credentials: false` |
| 并发 | `concurrency: deploy-production`，`cancel-in-progress: true`（新部署会取消进行中的部署） |
| 代码检出 | `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`（v4.2.2，固定 SHA） |
| 构建 | `docker build -t delta-service .`（基础镜像先尝试从腾讯云镜像预拉） |
| 冒烟 | 用同一 `db.env` 起临时容器 `delta-service-smoke`（宿主 18000→容器 8000），轮询 `/docs` 最多 30×2s |
| 部署 | `docker rm -f delta-service-container` 后 `docker run -d --name delta-service-container -p 8000:8000 --restart unless-stopped --env-file /etc/delta-service/db.env -e FORWARDED_ALLOW_IPS=172.17.0.1 delta-service` |
| 健康检查 | 轮询 `http://127.0.0.1:8000/health` 最多 30×2s，非 200 则打印容器日志并失败 |

流水线步骤与 `deploy.yml` 的对应关系：

| 步骤 | 行号（`deploy.yml`） | 说明 |
|---|---|---|
| Checkout | 29-32 | 固定 SHA、不持久化 token |
| Preflight | 34-42 | 检查 docker、启动 docker、检查 `$DB_ENV_FILE` 可读（`/etc/delta-service/db.env`） |
| Build image | 44-53 | 先 `docker pull mirror.ccs.tencentyun.com/library/python:3.11-slim` 并 tag 为 `python:3.11-slim`，再构建 |
| Smoke test | 55-73 | 临时容器 + `/docs` 轮询，失败输出容器日志 |
| Deploy | 75-98 | 替换 `delta-service-container` + `/health` 轮询 |
| Notify | 100-107 | 打印成功/失败 |

**流水线不会运行单元测试**（`python -m unittest` 需在本地/PR 阶段执行），这是当前 CI 的技术债，见 [FEATURES.md](FEATURES.md)。

## 2. 前置条件

### 2.1 部署机

| 需求 | 说明 |
|---|---|
| Docker | `docker` 命令可用，daemon 正常运行；runner 用户能执行 `docker`（在 `docker` 组或通过 sudo 白名单） |
| 端口 | 宿主机 `8000`（HTTP + WebSocket `/ws`）未被其它进程占用；旧 8765 默认关闭 |
| 磁盘 | 保留足够空间给镜像；定期 `docker image prune` |
| 时区 | 容器内使用系统默认时区；应用日志时间取进程本地时间 |

### 2.2 GitHub Actions Runner

| 项 | 说明 |
|---|---|
| 版本 | 需支持 `actions/checkout@v4.2.2` 与 Node 24 运行时（`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true`）。GitHub 官方对 Node 24 版 action 的最低 runner 要求是 **v2.327.1**（参见 actions/cache v5 的 runner 要求说明；最终以 GitHub 官方 release notes 为准）。若日志出现 Node 20 deprecated / runner outdated 警告，说明 runner 过旧，按 2.4 升级 |
| 标签 | 当前流水线用默认 `self-hosted`；建议给生产 runner 打独立标签（如 `delta-prod`）并改为 `runs-on: [self-hosted, delta-prod]`，避免测试 runner 误用生产凭据 |
| 权限 | runner 用户建议加入 `docker` 组；不要在流水线里做除 `systemctl start docker` 之外的 sudo 操作 |
| 工作目录 | 默认在 runner 安装目录的 `_work/`；磁盘不足时清理旧 workspace 与镜像 |

### 2.3 Runner 自动更新被关闭（`disableUpdate`）

部署机 runner 的配置（`.runner` 文件）中设置了 `"disableUpdate": true`，原因：

1. **避免部署中途自动升级重启**：runner 自动更新会在 job 之间重启进程，可能让部署任务被中断（`The runner has received a shutdown signal`）；
2. **网络受限**：部署机访问 GitHub 下载更新包不稳定，自动更新失败会导致 runner 离线，且难以复现；
3. **版本可复现**：固定 runner 版本，升级由人工脚本执行并记录，便于审计。

代价是 runner 不会自动获得安全更新，必须按 2.4 定期/收到 deprecated 告警时人工升级。

### 2.4 升级 runner（`/root/update-runner.sh <版本>`）

```bash
# 以 root 执行；<版本> 取自 https://github.com/actions/runner/releases（下面只是示例）
sudo /root/update-runner.sh <版本>
```

脚本的约定行为（以部署机实际脚本为准）：

1. 停止 runner 服务（`systemctl stop actions.runner.*` 或 `./svc.sh stop`），避免文件被占用；
2. 通过 gh-proxy 下载对应版本的 `actions-runner-linux-x64-<版本>.tar.gz`，例如
   `https://gh-proxy.com/https://github.com/actions/runner/releases/download/v<版本>/actions-runner-linux-x64-<版本>.tar.gz`；
3. **用 GitHub Release 页面公布的 SHA256 做 `sha256sum -c` 校验**，校验失败立即中止（不要跳过校验）；
4. 解压覆盖 runner 目录（保留原有 `.runner`、`.credentials`、`.credentials_rsaparams`，不需要重新注册）；
5. 重新启动 runner 服务，并在 GitHub 仓库 **Settings → Actions → Runners** 确认版本已更新且状态为 Idle/Online；
6. 查看 `_diag/Runner_*.log` 确认无启动错误。

验证：

```bash
# runner 目录内（路径以部署机为准，通常是 /opt/actions-runner 或 /home/runner/actions-runner）
cat .runner | grep -E 'agentName|gitHubUrl'
./svc.sh status
```

> gh-proxy 只是下载加速通道，不能替代 SHA256 校验；下载源与 GitHub Release 公布的校验和必须一致。

### 2.5 数据库凭据文件 `/etc/delta-service/db.env`

数据库口令不进入 GitHub Secrets，也不出现在命令行参数里，而是由部署机上的 env-file 提供：

```bash
# /etc/delta-service/db.env 示例（真实口令由运维保管）
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=delta_app
DB_PASSWORD=<用密码管理器生成>
DB_NAME=delta_service
DB_CHARSET=utf8mb4
```

权限要求：

```bash
sudo install -d -m 0755 -o root -g root /etc/delta-service
sudo install -m 0640 -o root -g runneruser /etc/delta-service/db.env
# 验证 runner 用户可读、其它用户不可读
sudo -u runneruser test -r /etc/delta-service/db.env && echo readable
sudo stat -c '%U:%G %a' /etc/delta-service/db.env   # 期望 root:runneruser 640
```

- 属主 `root`、属组 runner 运行用户（示例为 `runneruser`）、权限 `0640`；
- `deploy.yml` 通过 `env.DB_ENV_FILE` 固定为 `/etc/delta-service/db.env`；
- 口令轮换后只需更新该文件并重启容器/重新部署，不需要改仓库或 GitHub Secrets；
- **不要把该文件提交到仓库**；`.gitignore` 已忽略 `.env*`，`.dockerignore` 也会排除它。

### 2.6 HTTPS（Caddy）与真实来源 IP

生产通过宿主机 **Caddy** 终结 TLS 并反代到容器 `127.0.0.1:8000`：

- 对外地址：**https://deltachat.icu:8443**
- 证书/私钥（腾讯云**静态证书**，非 acme 自动续期）：
  - `/etc/delta-tls/deltachat.icu_bundle.crt`（权限 `644`）
  - `/etc/delta-tls/deltachat.icu.key`（权限 `600`）
- Caddy：`/usr/local/bin/caddy`（v2.10.2），配置 `/etc/caddy/Caddyfile`，
  systemd 服务名 **`delta-tls.service`**（`systemctl enable --now delta-tls`）。
- 监听 **8443 TLS** → `reverse_proxy 127.0.0.1:8000`，Caddyfile 片段：

```caddyfile
https://deltachat.icu:8443 {
    tls /etc/delta-tls/deltachat.icu_bundle.crt /etc/delta-tls/deltachat.icu.key
    reverse_proxy 127.0.0.1:8000
}
```

- 安全组需放行：**22**（SSH）、**8000**（容器/本机反代源）、**8443**（HTTPS）。
- Caddy 在宿主机、经 docker 网桥访问容器，容器看到的来源 IP 是 `172.17.0.1`；
  必须信任该反代并读取 `X-Forwarded-For`，否则登录限流会把所有用户当成同一 IP。
- CI 与手动部署的 `docker run` 都必须带：
  `-e FORWARDED_ALLOW_IPS=172.17.0.1`
- `main.py` 的 uvicorn 显式设置 `proxy_headers=True` +
  `forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")`，
  不依赖隐式默认。

**证书续期（静态证书，1 年有效）**：到期前在腾讯云重新下载证书，替换
`/etc/delta-tls/` 下的 `deltachat.icu_bundle.crt` 与 `deltachat.icu.key`，
然后 `systemctl restart delta-tls`。若后续接入 DNSPod API Token，可改为
acme.sh 自动续期。

**部署后验证**：

```bash
# 1) HTTPS 健康检查（-I 只看响应头）
curl -sI https://deltachat.icu:8443/health

# 2) 触发登录后看容器日志：client_ip 应为真实公网 IP，而不是 172.17.0.1
docker logs --since 5m delta-service-container 2>&1 | grep -i login | tail -20

# 3) 同一来源 IP 连续第 6 次登录应返回 429（登录限流 5 次/分/IP）
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    https://deltachat.icu:8443/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"nonexistent","password":"wrong"}'
done
# 前 5 次 401，第 6 次 429 即正确
```

> 若 Caddy 与容器不在同一 docker 网络/网段，按实际网关 IP 调整
> `FORWARDED_ALLOW_IPS`（可用 `docker network inspect bridge` 查看 Gateway）。

### 2.7 数据库自动迁移与关键列自检

**新增字段必须同时改两处**：模型/DAO、`scripts/migrate_f.py`（幂等 ALTER），并把列加入
`service/startup_migration.py` 的 `REQUIRED_COLUMNS`，否则启动自检会报 ERROR。0.2.12 新增诊断表 `diagnostics_events`（DDL 在 `scripts/migrate_f.py`），已纳入启动迁移/自检。

- 服务启动（`main.init_services`）默认自动执行 `scripts/migrate_f.py`：
  `AUTO_MIGRATE_ON_STARTUP=true`（默认）。迁移失败只记录 ERROR、**不阻断服务启动**，
  随后关键列自检会把缺失列打印为 ERROR；可设 `AUTO_MIGRATE_ON_STARTUP=false` 关闭
  自动迁移（仍保留自检）。
- CI 在容器健康检查通过后再显式执行一次
  `docker exec delta-service-container python scripts/migrate_f.py` 并打印 stats；
  失败输出 GitHub `::warning`，不把部署 job 标记失败，但会提示手动处理。
- 手动兜底同样在容器启动后执行迁移（见 §4）。
- 启动日志关键字：`启动数据库迁移完成: {...}`、`数据库关键列自检通过`；
  缺列时：`数据库关键列缺失（可能未执行迁移）: ...`。

> 本次 `room_skill_states.squad_number` 缺列事故的根因就是「代码先上线、迁移未执行」；
> 部署自动迁移 + 启动自检 + CI 显式迁移三层兜底。

### 2.8 诊断数据（0.2.12 B6）

- `POST /api/v1/diagnostics/batch` 落库 `diagnostics_events`（MySQL），单次 ≤512KB / gzip ≤128KB / 6 次每分，字段白名单裁剪；无管理页面，SQL 查询。
- 保留 7 天；`MessageCleanupTask` 每日随消息/画板清理一并删除过期行，启动先跑一次。
- 表由启动自动迁移创建（`scripts/migrate_f.py`），部署后确认日志 `diagnostics_table_created` 或自检通过。
- 排查示例：

```sql
SELECT account_user_id, client_diag_id, kind, created_at
FROM diagnostics_events
WHERE created_at >= NOW() - INTERVAL 1 DAY
ORDER BY id DESC LIMIT 50;

SELECT * FROM diagnostics_events
WHERE client_diag_id = '<uuid>' ORDER BY id DESC LIMIT 200;
```

## 3. 部署流程（从 push 到上线）

1. 开发者合并 PR 到 `master`；
2. GitHub 调度 self-hosted runner，checkout 指定 SHA；
3. Preflight 校验 Docker 与 env-file；
4. 构建镜像 `delta-service:latest`（每次覆盖），期间从腾讯云镜像预拉 Python 基础镜像；
5. 用生产 env-file 起临时容器做冒烟测试（18000 端口），访问 `/docs`；
6. 删除旧容器，启动 `delta-service-container`（仅 8000，`unless-stopped`；带 `-e FORWARDED_ALLOW_IPS=172.17.0.1`）；WebSocket 走同一端口的 `/ws`；
7. 轮询 `/health`，失败则输出 `docker logs delta-service-container` 后 job 失败；
8. 部署后执行数据库迁移（**升级必做**；脚本幂等，可重复跑；`generate_schemas` 不会给已有表补列）：
   ```bash
   docker run --rm --env-file /etc/delta-service/db.env delta-service python scripts/migrate_f.py
   ```
9. 部署后建议人工执行一次 `bash /root/delta-deploy.sh --check`（如脚本支持）或仓库内的 `check_deployment.sh` 做巡检；
10. 若干员配置 `assets/operators.json` 有更新，执行 `docker restart delta-service-container`（**不做热加载**），再验证 `GET /api/v1/config/operators` 的 `version` 已变化。

## 4. 手动兜底（不依赖 runner）

当 runner 离线、GitHub Actions 排障期间或紧急修复时，可直接在部署机执行：

```bash
sudo bash /root/delta-deploy.sh
```

该脚本不依赖 GitHub Actions，等价于流水线的手工版，约定步骤：

```bash
cd /path/to/delta_service            # 部署机上的代码目录（或手动 git fetch/reset 到目标提交）
docker build -t delta-service .
docker rm -f delta-service-smoke 2>/dev/null || true
docker run -d --name delta-service-smoke -p 18000:8000 --env-file /etc/delta-service/db.env delta-service
# 轮询 http://127.0.0.1:18000/docs 直到 200（最多约 60s），失败则失败退出
docker rm -f delta-service-container 2>/dev/null || true
docker run -d --name delta-service-container \
  -p 8000:8000 --restart unless-stopped \
  --env-file /etc/delta-service/db.env \
  -e FORWARDED_ALLOW_IPS=172.17.0.1 delta-service
# 轮询 http://127.0.0.1:8000/health 直到 200
# 升级必做：执行幂等迁移（空库/已迁移库均安全）
docker run --rm --env-file /etc/delta-service/db.env delta-service python scripts/migrate_f.py
```

注意事项：

- **迁移脚本必须执行**：`generate_schemas` 不会给已有表补列（如 `users.nickname`、`rooms.deleted`、`room_skill_states.squad_number`）；脚本幂等，空库/已迁移库重复执行均安全；应用启动默认自动迁移（`AUTO_MIGRATE_ON_STARTUP=true`），CI 部署后也会显式跑一次；
- 手动部署后，下一次 push 触发的 CI 会再次覆盖容器；手动操作只作为临时兜底，事后再查 runner/CI 问题；
- 手动构建时同样会覆盖 `delta-service:latest`，回滚信息见第 5 节；
- 任何情况下都不要把 `DB_PASSWORD` 直接写在命令行（会进 shell history/进程列表），统一用 `--env-file`。

## 5. 回滚

> 注意：`scripts/migrate_f.py` 添加的 `users.nickname` / `rooms.deleted` 为**向前迁移**；回滚代码无需回滚列（旧代码会忽略新列），但不要把数据库回滚到更早快照以免丢新数据。


### 5.1 镜像/容器回滚（推荐先做）

当前流水线**不保留版本标签**（始终覆盖 `delta-service:latest`），回滚依赖本地旧镜像：

```bash
# 部署前（建议固化到发布流程）：记录当前镜像 ID
docker image inspect delta-service --format '{{.Id}}' | sudo tee /root/delta-service-image-id.bak

# 发布后需要回滚时：
OLD_ID=$(cat /root/delta-service-image-id.bak)
docker rm -f delta-service-container
docker run -d --name delta-service-container \
  -p 8000:8000 --restart unless-stopped \
  --env-file /etc/delta-service/db.env "$OLD_ID"
# 健康检查
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
```

如果没记录镜像 ID：`docker images --filter dangling=true` 找上一次构建的镜像，或 checkout 上一个提交重新构建。**建议后续给镜像打 git SHA 标签**（技术债，见 FEATURES.md）。

### 5.2 代码回滚

```bash
cd /path/to/delta_service
git fetch origin
git checkout <上一个已发布 commit>
sudo bash /root/delta-deploy.sh
```

### 5.3 ⚠️ 口令哈希上线后的回滚限制

口令哈希代码上线后，存量明文行会通过迁移脚本 `scripts/migrate_passwords.py` 或“登录成功即重哈希”逐步变成 `scrypt$...`。**只要发生了任一情况，就不能把代码回滚到“口令哈希化之前”的版本**：

- 旧版本登录用 `WHERE username=? AND password=?` 明文等值比较；
- 数据库里已是 `scrypt$...` 哈希，旧代码永远匹配不到 → 这些用户 401。

如需回滚，只能回滚到**包含哈希校验兼容逻辑**的版本（即包含 `security/passwords.py` 的版本），或 forward-fix。数据库不需要回滚：87 字符哈希兼容旧 `varchar(100)` 列，旧代码不读哈希也不影响表结构；但尚未被迁移/重哈希的明文行仍会被旧代码正常使用。

### 5.4 数据库

- 应用启动时 `Tortoise.generate_schemas()` 只**建表**，不做 ALTER，不会自动迁移列；
- 流水线不执行任何 DDL；
- 不要在生产手工 `DROP`/`TRUNCATE users`；迁移脚本只做 `UPDATE users.password`，执行前先备份。

## 6. 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| Actions 在 Checkout 步骤失败：`unable to access 'https://github.com/...': Failure when receiving data from the peer` | 部署机到 GitHub 的网络抖动/代理不稳定 | `actions/checkout` 自带重试，偶发失败直接 **Re-run job**；频繁出现时检查 DNS、代理、出口 IP 限流与 MTU |
| `docker build` 卡在 `FROM python:3.11-slim` / Docker Hub timeout | Docker Hub 不可达 | 流水线已先拉 `mirror.ccs.tencentyun.com/library/python:3.11-slim` 并 tag；也可在部署机手动 `docker pull mirror.ccs.tencentyun.com/library/python:3.11-slim && docker tag ... python:3.11-slim`；**不要**在流水线里改 `/etc/docker/daemon.json` 并重启 Docker（会影响同机其它容器） |
| runner 显示 offline / job 一直 queued | runner 服务停止、网络断开、磁盘满 | `cd <runner目录> && ./svc.sh status`、`systemctl status actions.runner.*`、`df -h`、`tail -100 _diag/Runner_*.log`，修复后 `./svc.sh start` |
| 日志/Annotations 提示 runner 或 action 被 deprecated（Node 20 deprecated、runner outdated） | runner 版本过旧，无法运行 Node 24 版 action | 按 2.4 用 `/root/update-runner.sh <版本>` 升级 runner；升级后重新 run |
| Preflight 报 `❌ 缺少可读的 /etc/delta-service/db.env` | env 文件缺失或权限不对 | 按 2.5 检查 `root:runneruser 0640`，`sudo -u runneruser test -r` 验证 |
| 冒烟测试失败（18000 无 200） | 应用启动即失败：DB 连不上/环境变量缺失/口令错误 | `docker logs delta-service-smoke`；应用对 DB 是 **fail-fast**，先看 `DatabaseConfigError` 或 MySQL 拒绝连接；确认 env-file 的 host/账号可达 |
| 部署后 `/health` 一直非 200 | 容器崩溃、端口占用、健康检查 URL 不对 | `docker ps -a`、`docker logs delta-service-container`、`ss -lntp \| grep :8000`；`/health` 由应用自身提供，进程起来才 200 |
| 容器反复重启（`restart unless-stopped`） | 启动即异常退出（DB/配置错误） | `docker logs --tail 200 delta-service-container`；修复 env-file/DB 后重新部署 |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | 8000 被其它进程占用 | `ss -lntp \| grep ':8000'`，停掉占用进程或调整端口（同时改流水线与防火墙） |
| WS 客户端连不上 | 客户端未用 `/ws` 路径，或反向代理没有转发 WebSocket Upgrade | 使用 `ws://<host>:8000/ws`（生产 `wss://<host>/ws`）；确认反代对 `/ws` 支持 Upgrade；旧 8765 默认关闭（`LEGACY_WS_ENABLED=true` 才启动，不建议） |
| 磁盘满 / 镜像堆积 | 每次构建产生新镜像，旧镜像 dangling | `docker image prune -f`（确认无回滚需求后）；建议保留最近 2 个镜像 ID |
| 部署后旧容器仍在跑 | 并发/手动操作冲突 | `docker ps --filter name=delta-service` 确认；删除手工起的旧容器 |

### 6.1 Runner deprecated 的具体判断与处理

- 症状：GitHub **Settings → Actions → Runners** 中该 runner 标记为 outdated；job 日志出现 `This runner version is deprecated`、`Node.js 20 actions are deprecated`；或 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` 生效后 job 直接失败；
- 处理：按 2.4 升级到最新稳定版并验证 Online；确认 `.runner` 中 `disableUpdate` 仍为 true（防止再次自动升级）；
- 预防：每季度检查一次 runner 版本与 GitHub release notes。

## 7. 部署后巡检

**HTTPS / 真实 IP 验证（每次部署后）**：

```bash
curl -fsS https://deltachat.icu:8443/health
# 预期 {"status":"healthy","service":"delta_service"}
```

然后触发一次登录并查看容器日志，来源 IP 应为真实公网 IP（不是 `172.17.0.1`）：

```bash
docker logs --since 5m delta-service-container 2>&1 | grep -E 'client_ip|登录|POST /api/v1/auth/login' | tail -20
```

若日志里出现 `172.17.0.1`，检查 `FORWARDED_ALLOW_IPS` 是否传入容器、Caddy 是否设置 `X-Forwarded-For`。



**配置中心 / 运维检查**：

```bash
# 配置中心版本/干员数（应为 17，version 随文件内容变化）
curl -fsS https://deltachat.icu:8443/api/v1/config/operators |   python -c 'import json,sys; d=json.load(sys.stdin); print(d["version"], len(d["operators"]), d["meta"].get("season"))'

# 容器重启（改 operators.json 后必须）
docker restart delta-service-container
```

**备份（待办）**：当前未配置自动备份。上线后需补 MySQL 定时备份（`mysqldump` + 加密/Object Storage）与恢复演练；聊天/画板/技能数据依赖 MySQL。

仓库内的 `check_deployment.sh` 是**手动诊断脚本**（不是 CI 步骤），检查：容器存在/状态/启动时间、8000 监听、`/docs` 200、容器日志中的 error、`docker stats`。用法：

```bash
bash check_deployment.sh
```

注意：

- 它假设容器名 `delta-service-container`、端口 8000（HTTP + `/ws`）；
- WS 与 HTTP 共用 8000；脚本不再单独探 8765。`nc` 探端口不能验证 `/ws` 握手，建议用 `websocat ws://localhost:8000/ws` 或客户端联调；
- `/docs` 200 只说明 HTTP 进程在跑，不检查数据库连接（应用启动时已 fail-fast）。

## 8. 与旧文档的关系

- `GITHUB_ACTIONS_SETUP.md`（SSH + `SERVER_HOST`/`SSH_PRIVATE_KEY` Secrets 部署）**已删除**。原因是当前方案是 self-hosted runner 在部署机本地构建/运行容器，不再通过 SSH 从 GitHub 托管 runner 推镜像，也不再使用 `SERVER_*`/`SSH_PRIVATE_KEY` Secrets。
- 新流程的凭据只有一个：部署机上的 `/etc/delta-service/db.env`（0640），不进入 GitHub。
- 若需要恢复到 SSH 部署，属于新方案设计，不在本文范围内。
