# Jackbot_V1 — GCP 部署诊断指南

## 🚨 为什么还没有交易？

根据代码分析，可能的原因按优先级排列：

### 1️⃣ **API 密钥缺失或错误** (最常见)
   - GCP VM 的 `.env` 文件中 `BINANCE_TESTNET_API_KEY` 或 `BINANCE_TESTNET_API_SECRET` 为空
   - 或在 docker-compose.yml 中没有正确加载

### 2️⃣ **网络连接失败**
   - GCP VM 无法连接到 `testnet.binancefuture.com`
   - 防火墙或路由问题

### 3️⃣ **WebSocket 连接失败**
   - 即使 REST API 连接成功，WebSocket 也可能失败
   - 导致无法接收行情数据，策略无法启动

### 4️⃣ **Telegram 配置缺失**
   - 虽然不影响交易，但会导致无法获取通知

---

## 🔧 快速诊断步骤

### **步骤 1: 本地诊断（不需要 GCP）**

```bash
cd jackbot

# 1a. 检查 .env 文件是否存在和正确
cat .env
# 应该看到类似：
# BINANCE_TESTNET_API_KEY=your_key_here
# BINANCE_TESTNET_API_SECRET=your_secret_here

# 1b. 运行诊断脚本
python scripts/run.py --diagnose
```

**预期输出：**
```json
{
  "timestamp": "2026-04-26T...",
  "environment_variables": {
    "BINANCE_TESTNET_API_KEY": "✓ SET",
    "BINANCE_TESTNET_API_SECRET": "✓ SET",
    "TELEGRAM_BOT_TOKEN": "✓ SET",
    "TELEGRAM_CHAT_ID": "✓ SET"
  },
  "exchange_connection": {
    "status": "✓ CONNECTED",
    "latency_ms": 120,
    "balance_usd": 150.0
  }
}
```

### **步骤 2: GCP VM 诊断（SSH 进 VM）**

```bash
# 2a. SSH 进 GCP VM
gcloud compute ssh bot-vm-testnet --zone=asia-northeast1-a

# 2b. 检查 docker-compose 环境变量
cd /opt/bot
cat jackbot/.env
# 或查看 docker-compose 配置
cat jackbot/docker-compose.yml

# 2c. 查看当前运行的容器
docker ps | grep jackbot

# 2d. 查看容器日志
docker logs jackbot-v1 --tail=100 -f
# 或者
docker compose -f jackbot/docker-compose.yml logs -f jackbot
```

### **步骤 3: 运行诊断（在 VM 内）**

```bash
# 3a. 进入容器
docker compose -f jackbot/docker-compose.yml exec jackbot bash

# 3b. 运行诊断脚本
python scripts/run.py --diagnose

# 3c. 测试 Testnet API 连接
python3 << 'EOF'
import os
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv("BINANCE_TESTNET_API_KEY")
api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

print(f"API Key present: {bool(api_key)}")
print(f"API Secret present: {bool(api_secret)}")

if api_key and api_secret:
    import requests
    try:
        resp = requests.get(
            "https://testnet.binancefuture.com/fapi/v1/ping",
            headers={"X-MBX-APIKEY": api_key},
            timeout=5
        )
        print(f"Ping status: {resp.status_code}")
    except Exception as e:
        print(f"Connection error: {e}")
EOF
```

---

## 🔴 常见问题排查

### **问题：API Key/Secret 为空**

**症状：**
```
exchange_connection_failed: missing_api_credentials
```

**解决方案：**

1. **本地 .env 文件方案（开发）：**
```bash
# 在 jackbot 目录下创建 .env
cat > jackbot/.env << 'EOF'
BINANCE_TESTNET_API_KEY=your_actual_testnet_key
BINANCE_TESTNET_API_SECRET=your_actual_testnet_secret
TELEGRAM_BOT_TOKEN=your_telegram_token
TELEGRAM_CHAT_ID=your_chat_id
EOF
```

2. **GCP Secret Manager 方案（生产）：**
```bash
# 在 GCP VM 上设置
gcloud secrets create binance-testnet-api-key --replication-policy=automatic
echo -n "your_actual_key" | gcloud secrets versions add binance-testnet-api-key --data-file=-

gcloud secrets create binance-testnet-api-secret --replication-policy=automatic
echo -n "your_actual_secret" | gcloud secrets versions add binance-testnet-api-secret --data-file=-
```

3. **或者修改 docker-compose.yml：**
```yaml
# jackbot/docker-compose.yml
services:
  jackbot:
    environment:
      - BINANCE_TESTNET_API_KEY=${BINANCE_TESTNET_API_KEY}
      - BINANCE_TESTNET_API_SECRET=${BINANCE_TESTNET_API_SECRET}
```

### **问题：连接超时或 refused**

**症状：**
```
exchange_connection_failed: Connection timeout / Connection refused
```

**解决方案：**

1. **检查网络连接：**
```bash
# 在 VM 内测试
curl -v https://testnet.binancefuture.com/fapi/v1/ping
```

2. **检查 DNS：**
```bash
nslookup testnet.binancefuture.com
```

3. **检查防火墙规则：**
```bash
gcloud compute firewall-rules list --filter="name:bot"
# 确保允许出站 HTTPS (443)
```

### **问题：WebSocket 无法连接**

**症状：**
```
kline_feed_error: Connection failed / timeout
```

**解决方案：**

1. **测试 WebSocket 连接：**
```bash
python3 << 'EOF'
import asyncio
import websockets

async def test_ws():
    url = "wss://fstream.binancefuture.com/ws/btcusdt@kline_5m"
    try:
        async with websockets.connect(url) as ws:
            print("✓ WebSocket connected")
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"✓ Received message: {msg[:100]}")
    except Exception as e:
        print(f"✗ WebSocket error: {e}")

asyncio.run(test_ws())
EOF
```

---

## 📋 完整启动清单

- [ ] **本地验证**
  - [ ] 运行 `python scripts/run.py --diagnose` 并检查所有项目为 ✓
  - [ ] 运行 `python scripts/run.py --dry-run` 验证策略逻辑

- [ ] **部署到 GCP**
  - [ ] 创建 VM：`gcloud compute instances create bot-vm-testnet ...`
  - [ ] 配置静态 IP
  - [ ] 在 Secret Manager 中设置 4 个 secrets（API keys 等）
  - [ ] 授权 VM 服务帐户访问 Secret Manager

- [ ] **VM 初始化**
  - [ ] SSH 进 VM：`gcloud compute ssh bot-vm-testnet --zone=...`
  - [ ] Clone repo：`git clone <repo> /opt/bot`
  - [ ] 设置 .env 或从 Secret Manager 读取
  - [ ] 运行 `docker compose build` 和 `docker compose up -d jackbot`

- [ ] **验证运行**
  - [ ] 检查容器日志：`docker logs jackbot-v1 -f`
  - [ ] 收到 Telegram "Jackbot_V1 啟動" 消息
  - [ ] Dashboard 可访问：`http://<静态IP>:8501` (如果有)
  - [ ] 查看网格状态：在 Telegram 上发送 `/status`

---

## 🚀 快速修复：一键重启

```bash
# 在 GCP VM 上
cd /opt/bot/jackbot
docker compose down
docker compose up -d jackbot
docker logs jackbot-v1 -f
```

---

## 📞 进一步调试

如果上述步骤都检查过了，收集以下信息：

```bash
# 在 GCP VM 内
docker logs jackbot-v1 --since 10m > /tmp/bot_logs.txt
docker compose -f docker-compose.yml exec jackbot python scripts/run.py --diagnose > /tmp/diagnose.json
cat /tmp/bot_logs.txt /tmp/diagnose.json
```

然后分享日志内容进行进一步分析。

