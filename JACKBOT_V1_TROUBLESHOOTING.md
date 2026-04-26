# Jackbot_V1 — 为什么没有交易？问题诊断和解决方案

## 📋 **问题概览**

根据代码分析，**jackbot_V1 在 GCP VM 上部署后没有交易的最可能原因是：**

### **按优先级排列：**

| 优先级 | 问题 | 概率 | 症状 |
|--------|------|------|------|
| 🔴 1 | **API Key/Secret 缺失或错误** | 90% | 启动日志显示 `missing_api_credentials` 或 `exchange_connection_failed` |
| 🟠 2 | **网络连接失败** | 7% | 无法连接到 `testnet.binancefuture.com` |
| 🟡 3 | **WebSocket 连接失败** | 2% | REST API 连接正常但无法接收行情数据 |
| 🟢 4 | **其他配置问题** | 1% | Telegram token、权限、IP 白名单等 |

---

## 🔧 **快速修复步骤（5 分钟）**

### **步骤 1：SSH 连接到 GCP VM**

```bash
gcloud compute ssh bot-vm-testnet --zone=asia-northeast1-a
```

### **步骤 2：检查 API Key 配置**

```bash
cd /data/app

# 检查 .env 文件
cat jackbot/.env | head -5

# 如果输出是空的或缺失，这就是问题所在！
# 应该看到：
# BINANCE_TESTNET_API_KEY=xxx
# BINANCE_TESTNET_API_SECRET=xxx
```

### **步骤 3：设置 API Key（如果缺失）**

**方案 A：从 GCP Secret Manager（推荐）**

```bash
# 创建或更新 Secret Manager 中的 secret
gcloud secrets create binance-testnet-api-key --replication-policy=automatic 2>/dev/null || true
echo -n "你的_testnet_api_key" | gcloud secrets versions add binance-testnet-api-key --data-file=-

gcloud secrets create binance-testnet-api-secret --replication-policy=automatic 2>/dev/null || true
echo -n "你的_testnet_api_secret" | gcloud secrets versions add binance-testnet-api-secret --data-file=-

# 在 VM 上读取
gcloud secrets versions access latest --secret="binance-testnet-api-key" > /tmp/key.txt
gcloud secrets versions access latest --secret="binance-testnet-api-secret" > /tmp/secret.txt

# 创建 .env 文件
cat > /data/app/jackbot/.env << 'EOF'
BINANCE_TESTNET_API_KEY=$(cat /tmp/key.txt)
BINANCE_TESTNET_API_SECRET=$(cat /tmp/secret.txt)
TELEGRAM_BOT_TOKEN=your_telegram_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
EOF
```

**方案 B：直接编辑 .env（快速）**

```bash
# 获取你的 Testnet API Key 和 Secret
# 1. 访问 https://testnet.binancefuture.com/
# 2. 进入 API Management
# 3. 创建新 Key，获取 API Key 和 Secret

# 编辑 .env 文件
nano /data/app/jackbot/.env

# 粘贴以下内容（用真实的值替换）：
BINANCE_TESTNET_API_KEY=your_actual_testnet_api_key_here
BINANCE_TESTNET_API_SECRET=your_actual_testnet_api_secret_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# 保存：Ctrl+O → Enter → Ctrl+X
```

### **步骤 4：重启 Bot**

```bash
cd /data/app

# 停止旧容器
docker compose down jackbot

# 启动新容器（会读取更新的 .env）
docker compose up -d jackbot

# 等待启动
sleep 3

# 查看日志确认启动成功
docker compose logs -f jackbot --tail=50
```

### **步骤 5：验证运行**

查看日志中应该出现的成功标志：

```
✓ exchange_connected latency_ms=120 balance=150.0
✓ warmup_complete symbol=BTCUSDT bars=60
✓ warmup_complete symbol=ETHUSDT bars=60
🚀 Jackbot_V1 啟動
```

---

## 🔍 **完整诊断流程**

如果快速修复没有解决问题，运行完整诊断：

```bash
cd /data/app

# 运行诊断脚本
bash deploy/scripts/diagnose_gcp_vm.sh | tee /tmp/diagnosis.txt

# 查看诊断结果
cat /tmp/diagnosis.txt
```

**诊断脚本会检查：**
1. ✓ Docker 容器状态
2. ✓ 环境变量配置
3. ✓ 容器日志
4. ✓ 网络连接（Binance API 和 WebSocket）
5. ✓ 资源使用情况

---

## 📍 **关键文件位置**

```
/data/app/
├── jackbot/                    # Bot 应用目录
│   ├── .env                    # ← 环境变量（需要手动创建或配置）
│   ├── docker-compose.yml      # Docker 配置
│   ├── scripts/
│   │   ├── run.py             # Bot 主程序
│   │   └── run.py --diagnose  # 诊断工具
│   └── config/
│       └── settings.yaml       # 策略配置
├── deploy/
│   └── scripts/
│       ├── diagnose_gcp_vm.sh  # GCP VM 诊断脚本
│       ├── deploy.sh           # 部署脚本
│       └── bootstrap_vm.sh     # VM 初始化
└── docker-compose.yml          # 全局 compose 配置
```

---

## 🧪 **本地测试（在开发机上）**

如果想在部署到 GCP 前测试，在本地运行：

```bash
cd jackbot

# 1. 创建 .env 文件
cat > .env << 'EOF'
BINANCE_TESTNET_API_KEY=your_key
BINANCE_TESTNET_API_SECRET=your_secret
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
EOF

# 2. 运行诊断
python scripts/run.py --diagnose

# 输出应该显示：
# - API Key: ✓ SET
# - API Secret: ✓ SET
# - exchange_connection: ✓ CONNECTED
# - balance_usd: 150.0

# 3. Dry-run 模式（模拟交易，不真实下单）
python scripts/run.py --dry-run

# 应该看到很多类似的日志：
# dry_run_signal side=BUY price=45000 qty=0.5
```

---

## ❌ **常见错误和解决方案**

### **错误 1：`exchange_connection_failed`**

```
LOG: exchange_connection_failed: 401 Invalid API Key
```

**原因：** API Key 或 Secret 错误  
**解决：** 
- 验证 API Key 和 Secret 是否正确复制
- 检查是否使用了 Testnet 而不是 Mainnet 的 API Key
- 确保 API Key 有 "期货交易" 权限

### **错误 2：`kline_feed_error: Connection timeout`**

```
LOG: kline_feed_error: Connection failed at wss://fstream.binancefuture.com
```

**原因：** WebSocket 连接失败，可能是网络问题  
**解决：**
```bash
# 在 VM 内测试 WebSocket
curl -v https://fstream.binancefuture.com
# 应该返回 200 或 403（Forbidden 也可以）

# 如果超时，可能是防火墙问题
# 检查 GCP 防火墙规则是否允许出站 443
gcloud compute firewall-rules list --filter="name:bot"
```

### **错误 3：`docker-compose not found`**

```
bash: docker-compose: command not found
```

**原因：** Docker Compose 未安装或命令不同  
**解决：**
```bash
# 使用 docker compose 代替 docker-compose
docker compose version

# 或检查 docker-compose 安装
docker-compose version
```

---

## 🔐 **获取 Testnet API Key**

**完整步骤：**

1. **访问 Testnet**
   - 打开 https://testnet.binancefuture.com/

2. **注册或登录**
   - 如果没有账户，注册一个（Testnet 账户与 Mainnet 分离）

3. **进入 API Management**
   - 点击右上角用户头像 → 账户 → API 管理

4. **创建新 API Key**
   - 点击 "创建 API"
   - 标签：`Jackbot_V1` (可选)
   - 受限 IP：添加 GCP VM 的外部 IP
     ```bash
     # 查看 GCP VM 外部 IP
     gcloud compute instances describe bot-vm-testnet \
       --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
     ```
   - 权限：仅勾选 "期货交易（合约交易）"
   - **不要勾选** "提现" 或其他权限

5. **复制 API Key 和 Secret**
   - 复制 "API Key"
   - 点击 "编辑" → "编辑限制" → 查看 "API Secret"
   - 妥善保管这两个值

6. **更新 .env**
   ```bash
   # 在 GCP VM 上
   cat > /data/app/jackbot/.env << 'EOF'
   BINANCE_TESTNET_API_KEY=your_copied_api_key
   BINANCE_TESTNET_API_SECRET=your_copied_api_secret
   TELEGRAM_BOT_TOKEN=your_telegram_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   EOF
   ```

---

## 📊 **故障排查流程图**

```
bot 没有交易？
    ↓
[检查日志]
    ├─ missing_api_credentials → 【解决方案 1】创建 .env
    ├─ exchange_connection_failed → 【解决方案 2】验证 API Key
    ├─ kline_feed_error → 【解决方案 3】检查网络
    └─ 其他错误 → 【解决方案 4】检查日志详情
```

---

## 🚀 **下一步**

1. **立即执行：** 快速修复步骤（上面 5 个步骤）
2. **验证：** 查看 Telegram 是否收到 "🚀 Jackbot_V1 啟動" 消息
3. **监控：** 查看是否开始产生交易信号
4. **如有问题：** 运行完整诊断脚本

---

## 📞 **需要帮助？**

如果问题仍未解决，收集以下信息：

```bash
# 在 GCP VM 上运行
cd /data/app

# 1. 诊断信息
bash deploy/scripts/diagnose_gcp_vm.sh > /tmp/diagnosis.txt 2>&1

# 2. 完整日志
docker compose logs jackbot --tail=500 > /tmp/logs.txt 2>&1

# 3. 环境变量（不含密钥）
env | grep -E "^(BINANCE|TELEGRAM)" | sed 's/=.*/=***/' > /tmp/env.txt

# 显示诊断结果
echo "=== 诊断 ==="
head -50 /tmp/diagnosis.txt

echo ""
echo "=== 日志 ==="
head -100 /tmp/logs.txt

echo ""
echo "=== 环境 ==="
cat /tmp/env.txt
```

然后分享这些输出。

