# Jackbot_V1 — GCP VM 调试和恢复指南

## 🚀 快速启动检查清单

如果 bot 已部署但没有交易，按以下步骤操作：

### **第 0 步：SSH 连接到 GCP VM**

```bash
# 使用 gcloud CLI
gcloud compute ssh bot-vm-testnet --zone=asia-northeast1-a

# 或使用静态 IP 直接连接 (如果已配置)
ssh -i ~/.ssh/gcp_key ubuntu@<STATIC_IP>
```

---

## **第 1 步：运行完整诊断**

```bash
# 进入应用目录
cd /data/app

# 运行诊断脚本 (如果已部署)
bash deploy/scripts/diagnose_gcp_vm.sh | tee /tmp/diagnosis.txt

# 或手动运行
docker compose ps
docker compose logs -f jackbot --tail=100
```

---

## 🔍 **常见问题排查**

### **问题 1：容器无法启动**

```bash
# 查看详细错误
docker compose logs jackbot

# 如果看到类似错误：
# ERROR: Can't find a suitable configuration file
# 或
# ERROR: compose.yaml not found

# 解决方案：
cd /data/app
ls -la jackbot/docker-compose.yml  # 检查文件是否存在

# 重新拉代码
git pull origin main

# 重新启动
docker compose down
docker compose up -d jackbot
docker compose logs -f jackbot
```

### **问题 2：API 密钥错误**

```bash
# 查看是否缺少 .env 文件
cat jackbot/.env
# 应该显示：
# BINANCE_TESTNET_API_KEY=your_key
# BINANCE_TESTNET_API_SECRET=your_secret

# 如果 .env 不存在或为空，创建它
cat > jackbot/.env << 'EOF'
BINANCE_TESTNET_API_KEY=your_actual_testnet_api_key
BINANCE_TESTNET_API_SECRET=your_actual_testnet_api_secret
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
EOF

# 或从 GCP Secret Manager 读取
gcloud secrets versions access latest --secret="binance-testnet-api-key" > /tmp/api_key.txt
cat /tmp/api_key.txt
# 然后编辑 .env 填入这个值
```

### **问题 3：网络连接失败**

```bash
# 进入容器测试网络
docker compose exec jackbot bash

# 在容器内运行：
# 测试 Binance API
curl -v https://testnet.binancefuture.com/fapi/v1/ping

# 测试 DNS
nslookup testnet.binancefuture.com

# 测试 WebSocket
python3 << 'EOF'
import asyncio
import websockets

async def test():
    try:
        async with websockets.connect("wss://fstream.binancefuture.com/ws/btcusdt@kline_5m") as ws:
            print("✓ WebSocket 连接成功")
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            print("✓ 收到数据")
    except Exception as e:
        print(f"✗ WebSocket 错误: {e}")

asyncio.run(test())
EOF
```

### **问题 4：日志显示交易所连接失败**

```bash
# 查看完整日志
docker compose logs jackbot | tail -100 | grep -i "error\|fail\|connection"

# 如果看到 "exchange_connection_failed"：
# 1. 检查 API Key 是否正确
# 2. 检查 API Key 是否有交易权限（需要设置为支持合约交易）
# 3. 检查 API Key IP 白名单（必须包含 GCP VM 的外部 IP）

# 获取 VM 外部 IP
curl -s ifconfig.me
# 或
gcloud compute instances describe bot-vm-testnet --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

---

## 🛠️ **修复步骤（按优先级）**

### **1️⃣ 最可能的原因：API Key 缺失或错误**

```bash
cd /data/app

# 检查 .env 是否存在
ls -la jackbot/.env

# 检查内容（不显示密钥）
cat jackbot/.env | sed 's/=.*/=***/'

# 如果缺失，创建新 .env 文件
# 从本地笔记本或 GCP Secret Manager 获取真实的 API Key 和 Secret

# 使用 GCP Secret Manager（如果已配置）
gcloud secrets versions access latest --secret="binance-testnet-api-key" | tee jackbot/.env.tmp

# 手动编辑或创建
nano jackbot/.env
# 输入：
# BINANCE_TESTNET_API_KEY=<你的密钥>
# BINANCE_TESTNET_API_SECRET=<你的密钥>
# TELEGRAM_BOT_TOKEN=<你的令牌>
# TELEGRAM_CHAT_ID=<你的 chat id>
```

### **2️⃣ 重启 Bot**

```bash
cd /data/app

# 方案 A：使用 docker-compose
docker compose down jackbot
docker compose up -d jackbot
sleep 5
docker compose logs -f jackbot --tail=50

# 方案 B：使用 systemd (如果已配置)
sudo systemctl restart bot.service
sudo systemctl status bot.service
journalctl -u bot.service -f
```

### **3️⃣ 验证启动**

```bash
# 检查容器是否运行
docker compose ps | grep jackbot
# 应该看到：UP

# 检查是否有错误日志
docker compose logs jackbot | grep -i "exchange_connected"
# 应该看到：exchange_connected latency_ms=xxx balance=xxx

# 检查 Telegram 通知
# 应该在 Telegram 中收到 "🚀 Jackbot_V1 啟動" 消息
```

---

## 📊 **完整诊断和恢复流程**

```bash
#!/bin/bash
# 一键诊断和恢复脚本

cd /data/app

echo "=== 第 1 步：诊断 ==="
bash deploy/scripts/diagnose_gcp_vm.sh

echo ""
echo "=== 第 2 步：重启 ==="
docker compose down
docker compose up -d jackbot
sleep 3

echo ""
echo "=== 第 3 步：验证 ==="
docker compose logs -f jackbot --tail=30 &
LOGS_PID=$!
sleep 10
kill $LOGS_PID 2>/dev/null

echo ""
echo "=== 完成 ==="
docker compose ps
```

---

## 🧪 **测试交易（Dry-Run）**

如果诊断通过但没有真实交易，运行测试：

```bash
# 方案 1：本地测试（在开发机上）
cd jackbot
python scripts/run.py --diagnose
python scripts/run.py --dry-run

# 方案 2：在容器内测试
docker compose exec jackbot python scripts/run.py --diagnose
docker compose exec jackbot python scripts/run.py --dry-run

# 应该看到：
# 1. 所有环境变量 ✓ SET
# 2. exchange_connection: CONNECTED
# 3. 若干 dry_run_signal 信息
```

---

## 🔐 **安全检查（设置 API Key）**

### **不要在 GitHub 中提交 .env 文件！**

```bash
# .gitignore 应该包含
cat .gitignore | grep ".env"
# 应该看到：.env

# 验证 .env 不在 git 中
git status | grep ".env"
# 应该不显示任何内容

# 如果已提交，使用以下命令从历史中移除（谨慎操作）
# git filter-branch --force --index-filter "git rm --cached --ignore-unmatch .env" --prune-empty --tag-name-filter cat -- --all
```

### **获取 Testnet API Key**

1. 访问 https://testnet.binancefuture.com/
2. 注册或登录账户
3. 在右上角点击 "API" 或 "Account" → "API Management"
4. 创建新的 API Key：
   - 标签：Jackbot_V1
   - 权限：仅勾选 "期货交易"
   - IP 限制：添加 GCP VM 的外部 IP
5. 复制 API Key 和 Secret

---

## 📈 **监控 Bot 运行**

```bash
# 实时查看日志
docker compose logs -f jackbot

# 查看最近 N 行日志
docker compose logs jackbot --tail=100

# 查看特定时间范围
docker compose logs jackbot --since 2h

# 搜索错误
docker compose logs jackbot | grep -i "error"

# 查看容器内存使用
docker stats jackbot --no-stream
```

---

## 🚨 **紧急停止**

```bash
# 停止 bot（会平仓所有持仓）
docker compose stop jackbot

# 或通过 Telegram 命令（如果已实现）
# 在 Telegram 中发送：/killswitch

# 查看停止状态
docker compose ps jackbot
```

---

## 📞 **获取帮助**

如果上述步骤都尝试过但仍有问题，收集以下信息：

```bash
cd /data/app

# 收集诊断信息
bash deploy/scripts/diagnose_gcp_vm.sh > /tmp/diagnosis.txt 2>&1
docker compose logs jackbot --tail=200 > /tmp/bot_logs.txt 2>&1
env | grep -E "^(BINANCE|TELEGRAM)" | sed 's/=.*/=***/' > /tmp/env_vars.txt

# 显示文件
echo "=== 诊断结果 ==="
cat /tmp/diagnosis.txt

echo ""
echo "=== Bot 日志 ==="
head -50 /tmp/bot_logs.txt

# 或压缩发送
tar czf /tmp/jackbot_debug_$(date +%s).tar.gz \
  /tmp/diagnosis.txt \
  /tmp/bot_logs.txt \
  /tmp/env_vars.txt
```

