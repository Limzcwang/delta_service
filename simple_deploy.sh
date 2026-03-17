#!/bin/bash

# Delta Service 简单Docker部署脚本
# 适用于OpenCloud服务器部署

echo "=== Delta Service Docker部署 ==="

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "错误: Docker未安装，请先安装Docker"
    exit 1
fi

# 设置变量
IMAGE_NAME="delta-service"
CONTAINER_NAME="delta-service-container"
HTTP_PORT=8000
WS_PORT=8765

# 构建Docker镜像
echo "1. 构建Docker镜像..."
docker build -t $IMAGE_NAME .

if [ $? -ne 0 ]; then
    echo "错误: Docker镜像构建失败"
    exit 1
fi

echo "✓ 镜像构建成功"

# 停止并删除现有容器（如果存在）
echo "2. 清理现有容器..."
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true

# 运行容器
echo "3. 启动Docker容器..."
docker run -d \\
    --name $CONTAINER_NAME \\
    -p $HTTP_PORT:8000 \\
    -p $WS_PORT:8765 \\
    --restart unless-stopped \\
    $IMAGE_NAME

if [ $? -ne 0 ]; then
    echo "错误: 容器启动失败"
    exit 1
fi

echo "✓ 容器启动成功"

# 等待服务启动
echo "4. 等待服务启动..."
sleep 10

# 检查服务状态
echo "5. 检查服务状态..."

# 检查HTTP服务
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$HTTP_PORT/docs || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    echo "✓ HTTP API服务正常 (端口: $HTTP_PORT)"
else
    echo "⚠ HTTP API服务异常 (状态码: $HTTP_STATUS)"
fi

# 检查容器运行状态
CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' $CONTAINER_NAME 2>/dev/null || echo "unknown")
if [ "$CONTAINER_STATUS" = "running" ]; then
    echo "✓ Docker容器运行正常"
else
    echo "⚠ Docker容器状态: $CONTAINER_STATUS"
fi

# 显示部署信息
echo ""
echo "=== 部署完成 ==="
echo "服务信息:"
echo "- HTTP API文档: http://服务器IP:$HTTP_PORT/docs"
echo "- WebSocket服务: ws://服务器IP:$WS_PORT"
echo "- 容器名称: $CONTAINER_NAME"
echo ""

# 显示常用命令
echo "常用命令:"
echo "- 查看日志: docker logs $CONTAINER_NAME"
echo "- 停止服务: docker stop $CONTAINER_NAME"
echo "- 重启服务: docker restart $CONTAINER_NAME"
echo "- 进入容器: docker exec -it $CONTAINER_NAME /bin/bash"
echo ""

# 显示容器信息
echo "容器信息:"
docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"