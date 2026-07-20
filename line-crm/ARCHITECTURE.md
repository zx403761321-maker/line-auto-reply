# LINE 自动获客系统 — 架构文档

## 一、系统概述

一个运行在云服务器上的 LINE 自动化营销系统。通过 ADB 遥控多台云手机，实现自动加好友、AI 自动回复、意向客户识别。目前管理 10 台云手机，目标扩展至 100 台。

### 核心能力

- **自动加好友**：每天定时从名单读取 LINE ID，自动搜索并添加
- **AI 自动回复**：检测新消息，调用 DeepSeek AI 生成回复
- **防封风控**：随机化操作节奏、分散时段、模拟真人行为
- **设备管理**：支持动态注册新设备，无需重启服务

---

## 二、物理架构

```
┌─────────────────────────────────────────────────┐
│              云服务器 (日本/香港)                    │
│  ┌───────────────────────────────────────────┐   │
│  │         Docker 容器 (adb-bridge)            │   │
│  │  ┌─────────┐  ┌──────────────────────┐    │   │
│  │  │ main.py │  │      loop.py          │    │   │
│  │  │(Flask)  │  │  (自动回复巡检循环)     │    │   │
│  │  └────┬────┘  └──────────┬───────────┘    │   │
│  │       │                  │                 │   │
│  │       └──────┬───────────┘                 │   │
│  │              │ ADB 协议                     │   │
│  └──────────────┼────────────────────────────┘   │
│                 │                                  │
│    ┌────────────┼────────────┬──────────┐         │
│    │            │            │          │         │
│  云手机1    云手机2  ...  云手机10               │
│  (台湾IP)   (台湾IP)     (台湾IP)                │
│  LINE App   LINE App     LINE App                │
└─────────────────────────────────────────────────┘
                         │
                    LINE 服务器
```

### 关键组件

| 组件 | 位置 | 作用 |
|------|------|------|
| `main.py` | Docker 内 | Flask HTTP 服务，接收指令、遥控手机 |
| `loop.py` | Docker 内 | 死循环，轮流检查每台手机的新消息 |
| `daily_add_cloud-XX.sh` | 宿主机 | 每台手机的加好友脚本，cron 定时触发 |
| `batch_add.py` | 宿主机 | 手动批量加好友工具 |
| 云手机 | 远程 | 每台跑 LINE App，通过 ADB 被遥控 |

---

## 三、核心流程

### 3.1 加好友流程

```
cron 定时触发 (每天分散在 8:00-21:00)
    │
    └→ daily_add_cloud-03.sh
        │ ① 随机休眠 0-30 分钟（防止准点启动）
        │ ② 从 /tmp/targets_clean.txt 读取名单
        │ ③ 根据上次位置，取下一个 LINE ID
        │
        └→ HTTP POST → main.py:8899/line/add-friend-by-id
            │ ① 强制重启 LINE App
            │ ② 点击「主页」→「添加好友」→「搜索」
            │ ③ 点击「ID」标签，输入 LINE ID
            │ ④ 点击「搜索」
            │ ⑤ 如果找到：点击「添加」→ 确认 → 点「聊天」
            │ ⑥ 输入随机问候语（8 条网址文案随机选 1 条）
            │ ⑦ 发送
            │ ⑧ 随机浏览 1-3 秒（模拟真人）
            │
            └→ 等待 180-300 秒随机间隔 → 下一个

每天每台加 3-5 人，完成后自动停止。
```

### 3.2 自动回复流程

```
loop.py 无限循环（每台手机间隔约 60 秒）
    │
    └→ HTTP POST → main.py:8899/line/check-latest-chat
        │ ① 强制重启 LINE App（确保干净状态）
        │ ② 检查聊天 Tab 是否有红数字（未读消息总数）
        │     └─ 无：返回 no_unread，下一个设备
        │ ③ 点击聊天 Tab，逐屏滚动扫描绿数字
        │     └─ 最多滚 10 屏，兼容 text 和 content-desc 两种格式
        │ ④ 找到绿数字 → 点击聊天行 → 读取对方最后一条消息
        │ ⑤ 调用 DeepSeek API 生成回复
        │ ⑥ 发送回复
        │ ⑦ 判断对方意向等级 (L1-L5)，L3+ 记录到线索文件
        │ ⑧ 返回聊天列表，继续处理下一个绿数字
```

### 3.3 AI 回复逻辑

```
System Prompt 核心内容：
- 你是環球貸款小助理
- 公司方案：借 10000 實撥 7000 / 20000→14000 / 30000→21000
- 期限 8-10 天，首貸最高 50000
- 引導客戶至官網 https://dorrj.com 申請
- 或添加官方 LINE @583gyplg
- 不主動降價、不承諾一定通過、不提前收費
```

---

## 四、文件结构

```
/root/line-crm/                        ← 宿主机脚本和配置
├── scripts/
│   ├── daily_add.sh                   ← cloud-01 加好友入口 (9:00)
│   ├── daily_add_cloud-02.sh          ← cloud-02 (8:00)
│   ├── daily_add_cloud-03.sh          ← cloud-03 (13:00)
│   ├── daily_add_cloud-04.sh          ← cloud-04 (15:00)
│   ├── daily_add_cloud-05.sh          ← cloud-05 (19:00)
│   ├── daily_add_cloud-06.sh          ← cloud-06 (9:00)
│   ├── daily_add_cloud-07.sh          ← cloud-07 (11:00)
│   ├── daily_add_cloud-08.sh          ← cloud-08 (15:00)
│   ├── daily_add_cloud-09.sh          ← cloud-09 (17:00)
│   ├── daily_add_cloud-10.sh          ← cloud-10 (21:00)
│   ├── batch_add.py                   ← 手动批量添加工具
│   └── scheduler.sh                   ← 总调度器（备用）
├── config/
│   ├── personas.yaml                  ← 10 个人设定义（名字/性格/AI Prompt）
│   ├── greetings.txt                  ← 8 条网址文案（加好友首发用）
│   ├── accounts.yaml                  ← LINE 账号配置
│   └── devices.yaml                   ← 设备注册表
├── worker/
│   ├── line_ui.py                     ← LINE App UI 操作封装（旧版）
│   ├── adb_helper.py                  ← ADB 底层操作（旧版）
│   └── __init__.py
├── data/
│   ├── state/                         ← 各设备进度文件
│   └── leads/                         ← 意向客户线索
└── logs/

/workspace/openclaw/                    ← Docker 项目
├── docker-compose.yml
└── services/adb-bridge/
    ├── Dockerfile
    ├── main.py                        ← 核心：Flask API + ADB 遥控 + AI 回复
    ├── loop.py                        ← 自动回复巡检循环
    └── requirements.txt

/tmp/targets_clean.txt                 ← 实际工作的名单文件（4,586 条）
```

---

## 五、设备配置表

| 设备 | 加好友 | 自动回复 | 时段 | 人设 | 状态 |
|------|--------|---------|------|------|------|
| cloud-01 | 🔴 | 🔴 | — | 林美玲 | 被封 |
| cloud-02 | 🟢 | 🟢 | 8:00 | 王雅琳 | 正常 |
| cloud-03 | 🟢 | 🟢 | 13:00 | 陳小琪 | 正常 |
| cloud-04 | 🔴 | 🔴 | — | 張佳欣 | 被封 |
| cloud-05 | 🟢 | 🟢 | 19:00 | 吳心怡 | 正常 |
| cloud-06 | 🟢 | 🟢 | 9:00 | 林小雯 | 正常 |
| cloud-07 | 🟢 | 🟢 | 11:00 | 陳小雨 | 正常 |
| cloud-08 | 🟢 | 🟢 | 15:00 | 周小瑜 | 正常 |
| cloud-09 | 🟢 | 🟢 | 17:00 | 黃小芳 | 正常 |
| cloud-10 | 🟢 | 🟢 | 21:00 | 劉小菁 | 正常 |

8 台活跃，每天 8 × 3~5 = 24~40 人。

---

## 六、风控策略

| 策略 | 实现方式 |
|------|---------|
| 每日限量 | 每台每天 3-5 人，随机变化 |
| 时段分散 | 8:00-21:00，每小时一台 |
| 启动随机 | 每次启动先休眠 0-30 分钟 |
| 节奏随机 | 加人间隔 180-300 秒随机 |
| 操作随机 | 加好友完成后随机刷屏/发呆 |
| 绿数字检测 | 同时匹配 text 和 content-desc 双通道 |
| 消息读取 | 逐屏滚动扫描聊天列表，最多 10 屏 |
| 问候语轮换 | 8 条网址文案随机选 |

---

## 七、关键 API 端点

所有接口运行在 `http://127.0.0.1:8899`

| 端点 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/devices` | GET | 列出所有已注册设备 |
| `/device/register` | POST | 注册新设备 |
| `/line/add-friend-by-id` | POST | 通过 LINE ID 加好友并发问候 |
| `/line/check-latest-chat` | POST | 扫描未读消息并 AI 回复 |
| `/line/send-message` | POST | 给指定聊天发消息 |
| `/line/open` | POST | 打开 LINE App |
| `/line/close` | POST | 关闭 LINE App |
| `/line/screenshot` | GET | 截屏 |
| `/line/tap` | POST | 点击坐标 |
| `/line/swipe` | POST | 滑动屏幕 |
| `/line/type` | POST | 输入文字（支持中文） |

### 注册新设备

```bash
curl -X POST http://127.0.0.1:8899/device/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"cloud-11","addr":"IP:端口","type":"cloud","label":"云手机11"}'
```

### 加好友

```bash
curl -X POST "http://127.0.0.1:8899/line/add-friend-by-id?device=cloud-03" \
  -H "Content-Type: application/json" \
  -d '{"line_id":"目标LINE_ID","message":"问候语"}'
```

### 检查消息

```bash
curl -X POST "http://127.0.0.1:8899/line/check-latest-chat?device=cloud-03"
```

---

## 八、扩至 100 台注意事项

1. **服务器性能**：当前 1 台服务器管 10 台云手机，100 台可能需要 2-3 台服务器分担，或者升级配置
2. **loop.py 巡检压力**：当前串行巡检，7 台设备一轮约 8 分钟。100 台需要并行化或分组
3. **名单消耗**：每天 100 台 × 3-5 人 = 300-500 人/天，需要持续补充名单
4. **IP 质量**：每台云手机需要独立的台湾住宅 IP，100 个 IP 成本较高
5. **设备注册**：新机到后只需一条 `/device/register` 命令即可接入
6. **新增步骤**：
   - 注册设备：`curl /device/register`
   - 创建 `daily_add_cloud-XX.sh`（复制模板改 DEVICE）
   - 添加 cron 条目
   - 在 `loop.py` 的 DEVICES 和 ADD_SLOTS 中加一行
   - 在 `personas.yaml` 中加人设
   - 重建 Docker

---

## 九、日常维护命令

```bash
# 看加好友日志
tail -f /root/line-crm/logs/daily_add.log

# 看自动回复日志
docker exec openclaw-adb-bridge tail -f /tmp/loop.log

# 看意向客户
cat /root/line-crm/data/leads/leads.jsonl

# 看名单剩余
echo "总数: $(grep -vc '^#' /tmp/targets_clean.txt)"
echo "已处理: $(cat /root/line-crm/data/state/targets_position_shared)"

# 看设备连接状态
curl -s http://127.0.0.1:8899/devices | python3 -m json.tool

# 禁用一个设备（创建 banned 文件）
touch /workspace/openclaw/data/state/cloud-04_banned

# 恢复一个设备
rm /workspace/openclaw/data/state/cloud-04_banned

# 更新 cron
crontab -e

# 重建 Docker（改 main.py 或 loop.py 后）
cd /workspace/openclaw && docker compose build adb-bridge && docker compose up -d adb-bridge
```
