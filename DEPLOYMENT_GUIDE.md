# Delta Service OpenCloud 部署指南

## 概述

本文档详细说明如何将Delta Service部署到OpenCloud平台。Delta Service是一个实时通信服务，提供WebSocket实时通信和HTTP REST API接口。

## 前置要求

### 1. 系统要求

- **OpenCloud账户**: 有效的OpenCloud平台账户
- **kubectl**: Kubernetes命令行工具
- **Docker**: 容器化工具
- **Git**: 版本控制工具

### 2. 工具安装

```bash
# 安装kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# 安装Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 验证安装
kubectl version --client
docker --version
```

### 3. OpenCloud配置

1. 登录OpenCloud控制台
2. 获取kubeconfig配置文件
3. 配置kubectl访问权限

```bash
# 配置kubectl
mkdir -p ~/.kube
cp opencloud-kubeconfig.yaml ~/.kube/config
kubectl config use-context opencloud-context
```

## 部署步骤

### 方法一：使用自动化脚本（推荐）

#### 1. 完整部署流程

```bash
# 给脚本执行权限
chmod +x deploy.sh

# 执行完整部署流程
./deploy.sh --all
```

#### 2. 分步部署

```bash
# 仅构建镜像
./deploy.sh --build

# 仅部署到OpenCloud
./deploy.sh --deploy

# 构建并部署
./deploy.sh --build --deploy

# 测试部署
./deploy.sh --test

# 清理部署
./deploy.sh --clean
```

### 方法二：手动部署

#### 1. 构建Docker镜像

```bash
# 构建镜像
docker build -t delta-service:latest .

# 验证镜像
docker images | grep delta-service
```

#### 2. 推送镜像到镜像仓库（可选）

```bash
# 登录镜像仓库
docker login your-registry.com

# 标记镜像
docker tag delta-service:latest your-registry.com/delta-service:latest

# 推送镜像
docker push your-registry.com/delta-service:latest
```

#### 3. 部署到OpenCloud

```bash
# 应用配置文件
kubectl apply -f opencloud.yaml

# 等待部署完成
kubectl wait --for=condition=ready pod -l app=delta-service --timeout=300s

# 检查部署状态
kubectl get pods
kubectl get services
```

## 服务访问

### 1. 获取服务地址

```bash
# 查看服务信息
kubectl get service delta-service

# 获取外部IP和端口
EXTERNAL_IP=$(kubectl get service delta-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
HTTP_PORT=$(kubectl get service delta-service -o jsonpath='{.spec.ports[?(@.name=="http")].port}')
WS_PORT=$(kubectl get service delta-service -o jsonpath='{.spec.ports[?(@.name=="websocket")].port}')

echo "HTTP API: http://$EXTERNAL_IP:$HTTP_PORT"
echo "WebSocket: ws://$EXTERNAL_IP:$WS_PORT"
```

### 2. 访问服务

- **API文档**: http://<EXTERNAL_IP>:<HTTP_PORT>/docs
- **WebSocket连接**: ws://<EXTERNAL_IP>:<WS_PORT>

## 配置说明

### 1. 端口配置

| 服务 | 容器端口 | 服务端口 | 协议 | 说明 |
|------|----------|----------|------|------|
| HTTP API | 8000 | 80 | TCP | REST API接口 |
| WebSocket | 8765 | 8765 | TCP | 实时通信 |

### 2. 资源限制

| 资源类型 | 请求 | 限制 | 说明 |
|----------|------|------|------|
| CPU | 250m | 500m | 250毫核 = 0.25 CPU核心 |
| 内存 | 256Mi | 512Mi | 256MB ~ 512MB |

### 3. 存储配置

- **日志存储**: 1GB持久化存储
- **存储类**: standard
- **访问模式**: ReadWriteOnce

## 监控和维护

### 1. 查看服务状态

```bash
# 查看Pod状态
kubectl get pods -l app=delta-service

# 查看服务状态
kubectl get services delta-service

# 查看存储卷
kubectl get pvc -l app=delta-service

# 查看日志
kubectl logs -l app=delta-service --tail=100

# 查看事件
kubectl get events --sort-by='.lastTimestamp'
```

### 2. 健康检查

服务配置了以下健康检查：

- **存活探针**: 检查 /docs 端点，30秒间隔
- **就绪探针**: 检查 /docs 端点，5秒间隔

### 3. 自动扩缩容

- **最小副本数**: 1
- **最大副本数**: 3
- **目标CPU使用率**: 80%

## 故障排除

### 1. 常见问题

#### 问题1: Pod无法启动

```bash
# 检查Pod详情
kubectl describe pod <pod-name>

# 检查容器日志
kubectl logs <pod-name>
```

#### 问题2: 服务无法访问

```bash
# 检查服务配置
kubectl describe service delta-service

# 检查网络策略
kubectl get networkpolicies
```

#### 问题3: 镜像拉取失败

```bash
# 检查镜像仓库配置
kubectl describe pod <pod-name> | grep -A 5 -B 5 "Image"
```

### 2. 日志查看

```bash
# 实时查看日志
kubectl logs -l app=delta-service -f

# 查看特定时间段的日志
kubectl logs -l app=delta-service --since=1h

# 导出日志到文件
kubectl logs -l app=delta-service > delta-service.log
```

### 3. 调试命令

```bash
# 进入Pod调试
kubectl exec -it <pod-name> -- /bin/bash

# 端口转发（本地调试）
kubectl port-forward service/delta-service 8000:80

# 检查资源使用
kubectl top pods -l app=delta-service
```

## 更新和升级

### 1. 更新镜像

```bash
# 方法一：使用脚本
./deploy.sh --update

# 方法二：手动更新
kubectl set image deployment/delta-service delta-service=delta-service:latest
kubectl rollout status deployment/delta-service
```

### 2. 配置更新

```bash
# 更新配置文件
kubectl apply -f opencloud.yaml

# 检查更新状态
kubectl rollout history deployment/delta-service
```

## 安全考虑

### 1. 网络安全

- 仅开放必要的端口（80, 8765）
- 使用网络策略限制访问来源
- 考虑使用TLS加密通信

### 2. 访问控制

- 配置适当的RBAC权限
- 使用服务账户进行身份验证
- 定期轮换凭证

### 3. 数据安全

- 日志数据加密存储
- 敏感信息使用Secret管理
- 定期备份重要数据

## 性能优化建议

### 1. 资源优化

- 根据实际负载调整资源限制
- 监控CPU和内存使用情况
- 考虑使用HPA进行自动扩缩容

### 2. 网络优化

- 使用服务网格优化通信
- 配置适当的负载均衡策略
- 考虑使用CDN加速静态资源

### 3. 存储优化

- 根据日志量调整存储大小
- 配置日志轮转策略
- 考虑使用分布式存储

## 备份和恢复

### 1. 数据备份

```bash
# 备份配置
kubectl get -o yaml deployment/delta-service > delta-service-backup.yaml

# 备份存储卷（需要额外工具）
# 使用Velero或其他备份工具
```

### 2. 灾难恢复

```bash
# 恢复部署
kubectl apply -f delta-service-backup.yaml

# 检查恢复状态
kubectl get pods -l app=delta-service
```

## 联系方式

如果在部署过程中遇到问题，请联系：

- **项目维护者**: Delta Service Team
- **技术支持**: tech-support@example.com
- **文档更新**: 定期检查此文档获取最新信息

## 更新日志

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| 1.0.0 | 2024-01-01 | 初始版本发布 |

---

**注意**: 本文档会随着项目更新而更新，请定期查看最新版本。