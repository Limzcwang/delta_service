#!/bin/bash

# Delta Service 部署验证脚本

echo "=== Delta Service 部署验证 ==="

# 检查Docker容器状态
echo "1. 检查Docker容器状态..."

CONTAINER_NAME="delta-service-container"

# 检查容器是否存在
if docker ps -a | grep -q $CONTAINER_NAME; then
    echo "✓ 容器存在"
    
    # 检查容器运行状态
    STATUS=$(docker inspect -f '{{.State.Status}}' $CONTAINER_NAME 2>/dev/null)
    if [ "$STATUS" = "running" ]; then
        echo "✓ 容器运行中"
    else
        echo "⚠ 容器状态: $STATUS"
    fi
    
    # 检查容器启动时间
    STARTED=$(docker inspect -f '{{.State.StartedAt}}' $CONTAINER_NAME 2>/dev/null | cut -d'T' -f1)
    echo "✓ 容器启动时间: $STARTED"
    
else
    echo "✗ 容器不存在，请先运行部署脚本"
    exit 1
fi

# 检查端口映射
echo ""
echo "2. 检查端口映射..."

HTTP_PORT=8000
WS_PORT=8765

# 检查HTTP端口
if netstat -tulpn 2>/dev/null | grep -q ":$HTTP_PORT"; then
    echo "✓ HTTP端口 $HTTP_PORT 已监听"
else
    echo "⚠ HTTP端口 $HTTP_PORT 未监听"
fi

# 检查WebSocket端口
if netstat -tulpn 2>/dev/null | grep -q ":$WS_PORT"; then
    echo "✓ WebSocket端口 $WS_PORT 已监听"
else
    echo "⚠ WebSocket端口 $WS_PORT 未监听"
fi

# 检查服务健康状态
echo ""
echo "3. 检查服务健康状态..."

# 检查HTTP服务
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$HTTP_PORT/docs || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    echo "✓ HTTP API服务正常 (状态码: $HTTP_STATUS)"
else
    echo "⚠ HTTP API服务异常 (状态码: $HTTP_STATUS)"
fi

# 检查WebSocket服务（基础连接测试）
echo ""
echo "4. 检查WebSocket服务..."

# 简单的WebSocket连接测试
if command -v nc &> /dev/null; then
    # 使用nc检查端口是否可连接
    if echo | nc -w 1 localhost $WS_PORT &> /dev/null; then
        echo "✓ WebSocket端口 $WS_PORT 可连接"
    else
        echo "⚠ WebSocket端口 $WS_PORT 无法连接"
    fi
else
    echo "⚠ nc命令不可用，跳过WebSocket连接测试"
fi

# 检查容器日志
echo ""
echo "5. 检查容器日志..."

# 查看最近日志
echo "最近日志:"
docker logs --tail 5 $CONTAINER_NAME

# 检查错误日志
echo ""
echo "错误日志检查:"
ERROR_COUNT=$(docker logs $CONTAINER_NAME 2>&1 | grep -i "error" | wc -l)
if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "⚠ 发现 $ERROR_COUNT 个错误日志"
    docker logs $CONTAINER_NAME 2>&1 | grep -i "error" | tail -3
else
    echo "✓ 未发现错误日志"
fi

# 检查资源使用情况
echo ""
echo "6. 检查资源使用情况..."

echo "容器资源使用:"
docker stats $CONTAINER_NAME --no-stream --format "table {{.Container}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.NetIO}}"

# 汇总结果
echo ""
echo "=== 验证结果汇总 ==="

if [ "$STATUS" = "running" ] && [ "$HTTP_STATUS" = "200" ]; then
    echo "🎉 部署验证通过！服务运行正常"
    echo ""
    echo "服务访问信息:"
    echo "- HTTP API文档: http://服务器IP:$HTTP_PORT/docs"
    echo "- WebSocket服务: ws://服务器IP:$WS_PORT"
    echo ""
    echo "下一步操作:"
    echo "1. 在浏览器中打开API文档进行测试"
    echo "2. 使用WebSocket客户端连接测试"
    echo "3. 监控日志确保服务稳定运行"
else
    echo "❌ 部署验证失败，请检查以下问题:"
    [ "$STATUS" != "running" ] && echo "- 容器未运行"
    [ "$HTTP_STATUS" != "200" ] && echo "- HTTP服务不可用"
    echo ""
    echo "故障排除建议:"
    echo "1. 查看详细日志: docker logs $CONTAINER_NAME"
    echo "2. 检查端口冲突: netstat -tulpn | grep :$HTTP_PORT"
    echo "3. 重启容器: docker restart $CONTAINER_NAME"
fi

echo ""
echo "验证完成时间: $(date)"