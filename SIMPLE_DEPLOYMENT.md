# Delta Service 简单Docker部署指南

## 概述

这是一个简化的Docker部署方案，适用于OpenCloud服务器。只需几个简单步骤即可完成部署。

## 前置条件

- OpenCloud服务器已安装Docker
- 服务器开放端口：8000（HTTP API）、8765（WebSocket）

## 快速部署

### 方法一：使用一键部署脚本（推荐）

```bash
# 1. 给脚本执行权限
chmod +x simple_deploy.sh

# 2. 执行部署
./simple_deploy.sh
```

### 方法二：手动部署

```bash
# 1. 构建Docker镜像
docker build -t delta-service .

# 2. 运行容器
docker run -d \\
    --name delta-service-container \\
    -p 8000:8000 \\
    -p 8765:8765 \\
    --restart unless-stopped \\
    delta-service
```

## 服务访问

部署完成后，可以通过以下方式访问服务：

### HTTP API服务
- **API文档**: http://服务器IP:8000/docs
- **健康检查**: http://服务器IP:8000/health

### WebSocket服务
- **连接地址**: ws://服务器IP:8765

## 服务管理

### 查看服务状态
```bash
# 查看容器状态
docker ps

# 查看日志
docker logs delta-service-container

# 实时查看日志
docker logs -f delta-service-container
```

### 停止服务
```bash
docker stop delta-service-container
```

### 重启服务
```bash
docker restart delta-service-container
```

### 更新服务
```bash
# 停止旧容器
docker stop delta-service-container

# 删除旧容器
docker rm delta-service-container

# 重新构建镜像（如果代码有更新）
docker build -t delta-service .

# 启动新容器
docker run -d --name delta-service-container -p 8000:8000 -p 8765:8765 --restart unless-stopped delta-service
```

## 故障排除

### 常见问题

1. **端口冲突**
   ```bash
   # 检查端口占用
   netstat -tulpn | grep :8000
   netstat -tulpn | grep :8765
   
   # 如果端口被占用，可以修改映射端口
   docker run -d -p 8080:8000 -p 8766:8765 delta-service
   ```

2. **容器启动失败**
   ```bash
   # 查看详细错误信息
   docker logs delta-service-container
   
   # 检查镜像是否构建成功
   docker images | grep delta-service
   ```

3. **服务无法访问**
   ```bash
   # 检查防火墙设置
   iptables -L
   
   # 检查容器内部服务状态
   docker exec delta-service-container ps aux
   ```

### 日志查看

```bash
# 查看最近100行日志
docker logs --tail 100 delta-service-container

# 查看错误日志
docker logs delta-service-container | grep ERROR

# 实时监控日志
docker logs -f delta-service-container
```

## 配置说明

### 环境变量（可选）

如果需要自定义配置，可以通过环境变量：

```bash
docker run -d \\
    --name delta-service-container \\
    -p 8000:8000 \\
    -p 8765:8765 \\
    -e LOG_LEVEL=INFO \\
    -e MAX_CONNECTIONS=1000 \\
    --restart unless-stopped \\
    delta-service
```

### 数据持久化（可选）

如果需要持久化日志文件：

```bash
# 创建日志目录
mkdir -p /opt/delta-service/logs

# 运行容器并挂载日志目录
docker run -d \\
    --name delta-service-container \\
    -p 8000:8000 \\
    -p 8765:8765 \\
    -v /opt/delta-service/logs:/app/logs \\
    --restart unless-stopped \\
    delta-service
```

## 性能优化建议

### 资源限制

对于生产环境，建议设置资源限制：

```bash
docker run -d \\
    --name delta-service-container \\
    -p 8000:8000 \\
    -p 8765:8765 \\
    --memory=512m \\
    --cpus=1 \\
    --restart unless-stopped \\
    delta-service
```

### 监控设置

```bash
# 查看资源使用情况
docker stats delta-service-container

# 查看容器详细信息
docker inspect delta-service-container
```

## 安全建议

1. **使用非root用户运行**（已在Dockerfile中配置）
2. **定期更新基础镜像**
3. **配置防火墙规则**
4. **使用HTTPS/TLS加密**（生产环境建议）

## 技术支持

如果遇到问题，请检查：

1. Docker服务是否正常运行：`systemctl status docker`
2. 端口是否被占用：`netstat -tulpn | grep <端口号>`
3. 查看详细错误日志：`docker logs delta-service-container`

---

**注意**: 这是一个简化的部署方案，适用于快速部署和测试。生产环境建议使用更完善的容器编排方案。