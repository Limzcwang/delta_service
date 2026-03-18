#!/bin/bash

# Docker构建优化脚本
# 解决网络连接超时问题

echo "🚀 开始Docker构建优化配置..."

# 配置Docker守护进程使用国内镜像源
echo "🌐 配置Docker镜像源..."
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json << EOF
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://registry.docker-cn.com"
  ],
  "max-concurrent-downloads": 3,
  "max-download-attempts": 5,
  "dns": ["8.8.8.8", "114.114.114.114"]
}
EOF

# 重启Docker服务使配置生效
echo "🔄 重启Docker服务..."
sudo systemctl daemon-reload
sudo systemctl restart docker

# 等待Docker服务完全启动
echo "⏳ 等待Docker服务启动..."
sleep 15

# 检查Docker服务状态
if ! systemctl is-active --quiet docker; then
    echo "❌ Docker服务启动失败"
    exit 1
fi

echo "✅ Docker构建优化配置完成"

# 测试Docker Hub连接
echo "🔗 测试Docker Hub连接..."
if docker pull hello-world &> /dev/null; then
    echo "✅ Docker Hub连接正常"
else
    echo "⚠️ Docker Hub连接可能有问题，但继续构建过程..."
fi

echo "🎯 开始构建应用镜像..."

# 构建Docker镜像（带重试机制）
for i in {1..3}; do
    echo "尝试第 $i 次构建..."
    
    docker build \
        --network=host \
        --build-arg BUILDKIT_INLINE_CACHE=1 \
        --progress=plain \
        -t delta-service .
    
    if [ $? -eq 0 ]; then
        echo "✅ 镜像构建成功"
        break
    else
        echo "❌ 第 $i 次构建失败"
        if [ $i -lt 3 ]; then
            echo "⏳ 等待10秒后重试..."
            sleep 10
        else
            echo "❌ 所有构建尝试均失败"
            exit 1
        fi
    fi
done

echo "🎉 Docker构建完成！"