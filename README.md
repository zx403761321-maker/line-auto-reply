# LINE 自动获客系统 — 白话版说明书

---

## 这是什么？

一个自动帮你操作 LINE 的机器人。它能在 **15 台云手机**上自动加好友、自动聊天回复、发现意向客户就记录线索。

**相当于：雇了 15 个虚拟员工，每人每天最多加 20 个好友，有人发消息自动回，碰到想贷款的记录下来。**

---

## 📁 文件速查表（忘了就看这里）

### 核心大脑 — `services/adb-bridge/`

| 文件 | 一句话 |
|------|--------|
| `main.py` | **心脏**。所有 HTTP 接口：加好友、读消息、AI 回复、发消息。改 AI 话术就改这里的 system prompt |
| `scheduler.py` | **排班**。从 15 个账号里挑「最久没干活」的那个去干活 |
| `account_manager.py` | **记分牌**。记录每个账号今天加了几个人、搜了几次、有没有进冷却 |
| `adb_operator.py` | **遥控器**。真正操作手机：点、滑、打字、连 DeepSeek |
| `db.py` | 存任务日志、回复日志的数据库 |
| `loop.py` | 旧版值班员（现在值班逻辑挪进 main.py 里了） |

### 定时加好友 — `scripts/`

| 文件 | 一句话 |
|------|--------|
| `daily_add_cloud-01.sh` ~ `cloud-15.sh` | 每台手机的加好友入口，cron 每天定时跑，15 台错开 50 分钟 |
| `template.sh` | 加好友流程模板（生成 15 个 daily 脚本的母版；冷却/判死由 bridge 判定，脚本只按返回的 error 停/跳） |
| `batch_add.py` | 手动批量加好友工具 |
| `report.sh` | 生成每日日报 |
| `push_report.sh` | 把日报推到 LINE 上 |
| `inter_chat.py` | 账号互相聊几句，模拟真人活跃 |

### 配置 & 话术 — `config/`

| 文件 | 一句话 |
|------|--------|
| `devices.yaml` | **15 台设备地址表**（哪台手机连哪个 IP:端口） |
| `greetings.txt` | **8 条加好友首发文案**（随机选一条） |
| `knowledge_base.yaml` | 话术知识库（贷款方案、14 条话术、7 步流程，目前没被程序调用） |
| `settings.yaml` | 全局设置（注意：里面写了明文 API Key，别传出去） |

---

## 怎么运作的？

把整个系统想象成一个外卖店：

| 外卖店 | 你的系统 |
|--------|---------|
| 厨房 | 云服务器 — 所有指令从这里发出 |
| 15 个外卖小哥 | 15 台云手机 — 负责加好友 |
| 小哥的电瓶车 | ADB 连接 — 服务器遥控手机的通道 |
| 手机卡 | 台湾住宅 IP — 让 LINE 以为是台湾真人在用 |
| 出餐流程 | cron + Docker — 定时自动执行 |
| 菜品配方 | DeepSeek AI — 自动想怎么回复 |

---

## 两条主线

### 主线 1：自动加好友（每天在跑 ✅）

每天 8:00 开始，**每 50 分钟启动一台**，到 19:40 共 15 台错开跑：

1. 从名单 `/root/targets_all.txt` 取下一个 LINE ID
2. 随机选一句问候语
3. 调接口搜 ID → 添加 → 发问候 → 把好友名改成 LINE ID
4. 每台每天最多加 **20 人**，连续失败 10 次就停
5. 22:00 到 8:00 睡觉

**名单进度**：记在 `data/state/targets_position_shared`（一个共享游标，15 台轮流往后取，不重复）。

### 主线 2：自动回复（当前关着 ⏸️）

代码里有（`main.py` 的 `_auto_reply_loop`），但生产容器设了 `AUTO_REPLY_ENABLED=false`，**现在没在跑**。

想开启的话：改 `docker-compose.yml` 里 `AUTO_REPLY_ENABLED=true`，重建容器。

跑起来后流程：
1. 检查聊天 Tab 红数字（有没有未读）
2. 有 → 逐屏扫绿数字（找谁发了消息）
3. 进聊天读消息 → DeepSeek 生成回复 → 发送
4. AI 顺便判断意向等级 L1–L5

---

## 意向等级（L1–L5）

| 等级 | 意思 | 处理 |
|------|------|------|
| L1 | 完全无关（广告/诈骗） | 忽略 |
| L2 | 普通聊天 | 正常回 |
| L3 | 对贷款有兴趣 | 记录线索 |
| L4 | 很想贷款（给了部分资料） | 记录线索 |
| L5 | 马上要办（资料齐全） | 记录线索 |

线索存在 `data/leads/` 目录下（`leads.jsonl` 等）。

---

## 新手看这里：怎么操作

### 日常你什么都不用做

系统全自动：每天 8:00 开始加好友，9:30 出日报，19:40 收工。

### 偶尔你要做的

**看加了多少人：**
```bash
tail -f /root/line-crm/logs/daily_add.log
```

**看有没有意向客户：**
```bash
cat /root/line-crm/data/leads/leads.jsonl
```

**看 15 台账号状态：**
```bash
docker exec openclaw-adb-bridge python3 -c "import sqlite3;c=sqlite3.connect('/app/data/bridge.db');c.row_factory=sqlite3.Row;[print(dict(r)) for r in c.execute('SELECT device_id,status,daily_success,daily_search,risk_score FROM account_status ORDER BY device_id')]"
```

**系统崩了重启：**
```bash
cd /root/line-crm && docker compose restart adb-bridge
```

**加新的 LINE ID 到名单：**
编辑 `/root/targets_all.txt`，一行一个 ID。

**加一台新手机（16 号）：**
```bash
adb connect 新IP:端口
curl -X POST http://127.0.0.1:8899/device/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"cloud-16","addr":"IP:端口","type":"cloud","label":"云手机16"}'
```
然后：复制一份 `daily_add_cloud-XX.sh` 改 DEVICE → 加一条 cron → 在 `config/devices.yaml` 加条目 → 重建 Docker。

---

## 为什么这样设计？

1. **为什么用云手机**：封号了 3 分钟重建一个，自带台湾 IP，成本低。
2. **为什么用台湾住宅 IP**：LINE 检测上网 IP，台湾住宅 IP 让 LINE 以为是普通用户。
3. **为什么 15 台分散**：单台加太多会被 LINE 怀疑，分散到 15 台 + 错峰跑更安全。
4. **为什么服务器在日本/香港**：离台湾近，ADB 延迟低。
5. **为什么用 Docker**：打包成一个集装箱，换服务器直接搬。
6. **为什么要装 ADB**：ADB = 遥控器，服务器靠它操作手机。
7. **为什么分时段**：15 台同时跑容易一起出事，每 50 分钟错开一台。
8. **为什么识别绿数字不是红数字**：红数字=总未读，绿数字=具体谁发的，只回绿数字 < 999 的（大数字是广告群/官方号）。
9. **为什么用 DeepSeek**：便宜、中文好、支持繁体、API 好调。

---

## 常见问题

**Q: 为什么有时候加好友加到社群里去了？**
A: 页面加载慢点错地方。程序会自动检测，错了就重启重来。

**Q: 为什么有的好友没改成名？**
A: 笔形图标位置会随名字长短变，有时没点到。不影响加好友，LINE ID 在日志里能查到。

**Q: 换服务器怎么搬？**
A: 打包 `/root/line-crm` 和 `/root/targets_all.txt`，新服务器装 Docker + ADB，`docker compose up -d` 就行。

**Q: 搜索次数达上限了怎么办？**
A: 设备进入 5 天冷却期，累计 3 次冷却后判死（需换号）。详见 `ARCHITECTURE.md` 第五节。

---

## 完整架构

想看代码分层、数据库表、API 列表、已知问题，看 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。
