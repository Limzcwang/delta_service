# GitHub Actions 自动部署配置指南

## 概述

本指南说明如何配置GitHub Actions实现自动部署到服务器。当代码推送到master分支时，系统会自动构建Docker镜像并部署到指定服务器。

## GitHub Secrets 配置

在GitHub仓库中设置以下Secrets：

### 必需配置

1. **SERVER_HOST** - 服务器IP地址或域名
   - 示例：`192.168.1.100` 或 `your-server.com`

2. **SERVER_PORT** - SSH端口号
   - 示例：`22`（默认SSH端口）

3. **SERVER_USER** - 服务器用户名
   - 示例：`root` 或 `ubuntu`

4. **SSH_PRIVATE_KEY** - SSH私钥
   - 获取方法：`cat ~/.ssh/id_rsa`（服务器上的私钥内容）

### 配置步骤

1. 进入GitHub仓库页面
2. 点击 **Settings** → **Secrets and variables** → **Actions**
3. 点击 **New repository secret**
4. 依次添加上述4个secret

## 服务器准备

### 1. 安装Docker

确保目标服务器已安装Docker：

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install docker.io docker-compose
sudo systemctl enable docker
sudo systemctl start docker

# CentOS/RHEL
sudo yum install docker
sudo systemctl enable docker
sudo systemctl start docker
```

### 2. 配置SSH密钥

在服务器上生成SSH密钥对：

```bash
# 生成密钥对（如果还没有）
ssh-keygen -t rsa -b 4096 -C "github-actions@your-domain.com"

# 查看公钥
cat ~/.ssh/id_rsa.pub

# 将公钥添加到authorized_keys
cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### 3. 防火墙配置

确保服务器开放所需端口：

```bash
# 开放HTTP端口（8000）
sudo ufw allow 8000/tcp

# 开放WebSocket端口（8765）
sudo ufw allow 8765/tcp

# 开放SSH端口（默认22）
sudo ufw allow 22/tcp
```

## 工作流说明

### 触发条件

- **自动部署**：当代码推送到 `master` 分支时触发
- **测试构建**：当创建到 `master` 分支的Pull Request时触发构建测试

### 工作流程

1. **代码检出**：从GitHub仓库检出最新代码
2. **Docker构建**：构建Docker镜像
3. **镜像保存**：将镜像保存为tar文件
4. **服务器部署**：通过SSH连接到服务器并部署
5. **健康检查**：验证服务是否正常启动

### 手动触发

如果需要手动触发部署：

1. 进入GitHub仓库的 **Actions** 页面
2. 选择 **Deploy to Server** 工作流
3. 点击 **Run workflow**
4. 选择分支（默认为master）
5. 点击 **Run workflow**

## 故障排除

### 常见问题

#### 1. SSH连接失败

**错误信息**：`Permission denied (publickey)`

**解决方案**：
- 检查SSH私钥是否正确复制到GitHub Secrets
- 确保服务器上的公钥已添加到authorized_keys
- 验证服务器用户名和端口是否正确

#### 2. Docker权限问题

**错误信息**：`Got permission denied while trying to connect to the Docker daemon socket`

**解决方案**：
```bash
# 将用户添加到docker组
sudo usermod -aG docker $USER

# 重新登录或重启服务
sudo systemctl restart docker
```

#### 3. 端口冲突

**错误信息**：`Bind for 0.0.0.0:8000 failed: port is already allocated`

**解决方案**：
- 停止占用端口的其他服务
- 或修改Docker端口映射（需要更新工作流）

### 日志查看

#### GitHub Actions日志

1. 进入仓库的 **Actions** 页面
2. 点击最近的部署工作流
3. 查看各个步骤的详细日志

#### 服务器日志

```bash
# 查看容器日志
docker logs delta-service-container

# 实时查看日志
docker logs -f delta-service-container

# 查看容器状态
docker ps -a
```

## 自定义配置

### 修改端口映射

如果需要修改端口映射，更新工作流中的Docker运行命令：

```yaml
docker run -d \\
  --name delta-service-container \\
  -p 8080:8000 \\  # 外部端口:内部端口
  -p 8766:8765 \\
  --restart unless-stopped \\
  delta-service
```

### 添加环境变量

在Docker运行命令中添加环境变量：

```yaml
docker run -d \\
  --name delta-service-container \\
  -p 8000:8000 \\
  -p 8765:8765 \\
  -e LOG_LEVEL=INFO \\
  -e MAX_CONNECTIONS=1000 \\
  --restart unless-stopped \\
  delta-service
```

### 数据持久化

如果需要持久化数据，添加卷挂载：

```yaml
docker run -d \\
  --name delta-service-container \\
  -p 8000:8000 \\
  -p 8765:8765 \\
  -v /opt/delta-service/logs:/app/logs \\
  --restart unless-stopped \\
  delta-service
```

## 安全建议

1. **使用非root用户**：在生产环境中使用非root用户运行Docker容器
2. **定期更新**：定期更新基础镜像和安全补丁
3. **网络隔离**：考虑使用Docker网络隔离容器
4. **监控日志**：设置日志监控和告警
5. **备份策略**：制定数据备份和恢复策略

## 支持与反馈

如果遇到问题：

1. 查看GitHub Actions日志获取详细错误信息
2. 检查服务器上的Docker容器状态
3. 验证网络连接和防火墙设置
4. 参考Docker和GitHub Actions官方文档

---

**注意**：首次部署建议先进行测试，确保所有配置正确后再用于生产环境。