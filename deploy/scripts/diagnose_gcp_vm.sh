#!/bin/bash
# GCP VM Jackbot_V1 诊断脚本
# 在 GCP VM 上运行此脚本来诊断为什么 bot 没有交易

set -e

echo "=========================================="
echo "Jackbot_V1 — GCP VM 诊断工具"
echo "=========================================="
echo ""

APP_DIR="${APP_DIR:-/data/app}"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] 1️⃣  检查系统环境..."
echo "--------------------------------------"
echo "工作目录: $APP_DIR"
echo "当前目录: $(pwd)"
echo "Docker 状态:"
docker --version
docker compose --version
echo ""

echo "[$TIMESTAMP] 2️⃣  检查 bot 代码位置..."
echo "--------------------------------------"
if [ -d "$APP_DIR" ]; then
    echo "✓ 应用目录存在: $APP_DIR"
    ls -lh "$APP_DIR/" | head -20
else
    echo "✗ 应用目录不存在: $APP_DIR"
    echo "请检查部署脚本是否已运行"
    exit 1
fi
echo ""

echo "[$TIMESTAMP] 3️⃣  检查 Docker 容器状态..."
echo "--------------------------------------"
cd "$APP_DIR"

echo "正在运行的容器:"
docker compose ps
echo ""

# 检查 jackbot 容器
if docker compose ps jackbot 2>/dev/null | grep -q "jackbot"; then
    echo "✓ jackbot 容器正在运行"
else
    echo "✗ jackbot 容器未运行"
    echo "尝试启动容器..."
    docker compose up -d jackbot
    sleep 3
    docker compose ps
fi
echo ""

echo "[$TIMESTAMP] 4️⃣  检查容器日志..."
echo "--------------------------------------"
echo "最近 50 行日志:"
docker compose logs --tail=50 jackbot
echo ""

echo "[$TIMESTAMP] 5️⃣  检查环境变量..."
echo "--------------------------------------"
echo "检查 .env 文件:"
if [ -f "jackbot/.env" ]; then
    echo "✓ .env 文件存在"
    # 不显示完整值，只显示是否设置
    grep -E "^(BINANCE|TELEGRAM)" jackbot/.env | sed 's/=.*/=***/' || echo "✗ 未找到 API 密钥配置"
else
    echo "✗ .env 文件不存在: jackbot/.env"
fi
echo ""

echo "检查环境变量 (在容器内):"
docker compose exec -T jackbot env | grep -E "^(BINANCE|TELEGRAM)" || echo "✗ 未找到 API 密钥环境变量"
echo ""

echo "[$TIMESTAMP] 6️⃣  运行 bot 诊断..."
echo "--------------------------------------"
docker compose exec -T jackbot python scripts/run.py --diagnose 2>&1 || echo "✗ 诊断失败"
echo ""

echo "[$TIMESTAMP] 7️⃣  检查网络连接..."
echo "--------------------------------------"
echo "测试到 Binance Testnet 的连接:"
docker compose exec -T jackbot sh -c 'python3 -c "
import socket
import ssl

# 测试 REST API
print(\"Testing testnet.binancefuture.com...\")
try:
    context = ssl.create_default_context()
    with socket.create_connection((\"testnet.binancefuture.com\", 443), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=\"testnet.binancefuture.com\") as ssock:
            print(\"✓ REST API 连接正常\")
except Exception as e:
    print(f\"✗ REST API 连接失败: {e}\")

# 测试 WebSocket
print(\"Testing fstream.binancefuture.com...\")
try:
    context = ssl.create_default_context()
    with socket.create_connection((\"fstream.binancefuture.com\", 443), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=\"fstream.binancefuture.com\") as ssock:
            print(\"✓ WebSocket 连接正常\")
except Exception as e:
    print(f\"✗ WebSocket 连接失败: {e}\")
" 2>&1' || echo "网络测试脚本执行失败"
echo ""

echo "[$TIMESTAMP] 8️⃣  检查容器资源使用..."
echo "--------------------------------------"
docker stats --no-stream jackbot
echo ""

echo "[$TIMESTAMP] 9️⃣  最终状态总结..."
echo "--------------------------------------"
echo "容器状态:"
docker compose ps
echo ""

echo "最后 20 行错误日志:"
docker compose logs jackbot 2>&1 | grep -i "error" | tail -20 || echo "未发现错误日志"
echo ""

echo "=========================================="
echo "诊断完成！"
echo "=========================================="
echo ""
echo "📋 故障排查步骤:"
echo "1. 检查上面的诊断输出中是否有 ✗ 标记"
echo "2. 查看 '检查环境变量' 部分 - API Key/Secret 是否已设置"
echo "3. 查看 '最近 50 行日志' - 是否有错误信息"
echo "4. 查看 '网络连接' 测试 - 是否能连到 Binance"
echo ""
echo "💾 如要保存诊断结果:"
echo "  bash $0 | tee /tmp/jackbot_diagnosis_\$(date +%Y%m%d_%H%M%S).txt"
echo ""
