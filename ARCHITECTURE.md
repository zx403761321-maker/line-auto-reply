# LINE 自动获客系统 — 架构文档

> 最后更新：2026-08-27。本文档反映当前真实运行状态（15 台设备、四层分层架构）。

## 一、系统概述

运行在云服务器上的 LINE 自动化获客系统：通过 ADB 遥控多台云手机（每台一个 LINE 账号 + 台湾住宅 IP），自动完成「加好友 → AI 自动回复 → 识别贷款意向客户 → 生成日报」。目前配置 **15 台云手机**（`cloud-01` ~ `cloud-15`），目标扩展至 100 台。

### 核心能力

- **自动加好友**：每天定时从名单读取 LINE ID，自动搜索并添加、发问候、改名
- **AI 自动回复**：检测新消息，调用 DeepSeek AI 生成回复（生产环境当前关闭，见 §3.2）
- **意向客户识别**：AI 判断意向等级 L1–L5，线索落盘
- **风控**：随机节奏、时段分散、搜索上限冷却、换号标记
- **设备管理**：运行时注册/移除设备，无需重启

---

## 二、代码架构（四层分层）

最新重构（commit `f722002`）把单体 `main.py` 拆成清晰分层，边界由各文件顶部 docstring 约定。

```
main.py (业务层 / Flask)
   │  依赖 ↓
Scheduler (调度层)       —— 只读 AccountManager，选最优账号
   │
AccountManager (状态层)  —— 只读写 SQLite，不碰 ADB
   │
AdbOperator (ADB 层)     —— 只操作手机，禁止碰 DB/调度/Flask
   │
云手机 (uiautomator2 + adb 命令)
```

| 层 | 文件 | 职责 | 关键约定 |
|---|---|---|---|
| **业务层** | `services/adb-bridge/main.py` | Flask 全部 API、把请求串成完整业务流、最后调 `am.report()` 上报 | 全局异常兜底；每台设备一把 `threading.Lock` 防并发 |
| **调度层** | `services/adb-bridge/scheduler.py` | `pick_account()` / `pick_multi()` 选账号，按「最久未使用」排序 | 过滤 banned/disabled/dead/login_error/cooldown/search_limit/daily_limit |
| **状态层** | `services/adb-bridge/account_manager.py` | 账号状态机 + 每日统计 + 冷却 + 风险分 | `report()` 是统一入口，把 ADB 结果映射为状态变更 |
| **ADB 层** | `services/adb-bridge/adb_operator.py` | adb 命令、uiautomator2 元素定位、DeepSeek 调用、连接重连 | 方法全返回标准 `dict`，不写库 |
| **持久化** | `services/adb-bridge/db.py` | 三张独立日志表（设备状态/任务/回复） | 与 account 状态表分离 |
| **日志** | `services/adb-bridge/logger.py` | 结构化 key=value 日志 | `LOG_FORMAT=json` 可切 JSON |

### 账号状态机（`AccountManager.report()` 的核心映射）

```
ADB 结果 last_step/error    →  账号状态变更
────────────────────────────────────────────────────────────
ok + add_friend             → daily_success+1，连续失败清零，风险 -5
ok + check_chat             → total_reply_count + N
搜索达上限(search_limit)     → search_limit_count+1：<3 次→cooldown 5 天(风险+30)；≥3 次→dead 判死(风险+50)
登录失败(login_error)        → status=login_error
设备离线(device_offline)     → status=dead
账号被封(banned)             → status=banned
用户不存在(no_result)        → today_not_found+1，风险 +2（不算连续失败）
其他失败                     → daily_fail+1，连续失败+1，超阈值自动冷却
```

账号状态集合：`active / cooldown / search_limit / login_error / dead / banned / disabled / paused`。跨天自动重置每日计数（`daily_success/fail/search/not_found`）。`search_limit` 是旧状态（仅兼容老数据），新流程用 `cooldown` 状态累计 5 天冷却、达 3 次判死（见 §五）。

---

## 三、运营流程（两条主线）

### 3.1 加好友流程（cron + shell，当前唯一启用）

**触发**：宿主机 `crontab -l` 里 15 条条目，每天 **8:00 → 19:40 每 50 分钟启动一台**，各台错开；9:30 跑 `report.sh` 出日报。

```
crontab（每 50 分钟一台）
    │
    └→ scripts/daily_add_cloud-XX.sh
        │ ① 随机睡 0-30 秒（防准点）
        │ ② flock 加锁读共享进度 targets_position_shared → 取下一个 LINE ID
        │ ③ shuf 随机选 1 条问候语（config/greetings.txt，8 条）
        │
        └→ curl POST /line/add-friend-by-id?device=cloud-XX
            │ ① 强制重启 LINE
            │ ② 主页 → ➕ → 搜索 → ID 标签 → 输入 LINE ID → 搜索
            │ ③ 找到 → 添加 → 二次确认 → 聊天 → 发问候 → 改好友名为 LINE ID
            │ ④ 页面跑偏自动重启重来（验证「搜索」「ID」标签）
            │
            └→ 间隔 60-180 秒随机 → 下一个
```

- **每日目标**：`DAILY_GOAL=20`（每台每天最多加 20 人）
- **连续失败 ≥10 次自动停止**（`MAX_FAIL=10`）
- **搜索达上限**：脚本收到 `search_limit` 就 `break` 停当天
- **22:00–08:00** 睡眠
- **名单**：`/root/targets_all.txt`（8419 行），共享进度 `data/state/targets_position_shared`（当前 2144）

> ⚠️ 名单有历史分歧：`scripts/template.sh` 指向 `/root/line-crm/data/targets/targets_all.txt`（不存在），实际部署的 `daily_add_cloud-XX.sh` 指向 `/root/targets_all.txt`。以后以 `/root/targets_all.txt` 为准，别混用。

### 3.2 自动回复流程（容器内 daemon 线程，当前关闭）

**触发**：`main.py` 启动时拉起 `_auto_reply_loop` 线程，由环境变量 `AUTO_REPLY_ENABLED` 控制。

**当前生产容器 `AUTO_REPLY_ENABLED=false`，自动回复未运行。** 开启方式见 §六。

流程（`POST /line/check-latest-chat`）：
1. 23:00–08:00 休眠
2. 重启 LINE → 检查聊天 Tab「红数字」（未读总数）
3. 有红数字 → 进列表 → 逐屏滚扫「绿数字」（未读消息，最多 10 屏）
4. 逐个进聊天 → 读对方最后一条消息（左气泡 x≤320 判定）
5. 表情 → 回「你好」；否则调 DeepSeek 生成 2-3 句繁体回复
6. 第二次 DeepSeek 调用判断意向等级 L1–L5
7. 意向写 `leads.jsonl`（L3+ 为高意向）
8. 发送 → 返回列表处理下一个

**AI 话术**内嵌在 `main.py` 的 system prompt（公司方案、`dorrj.com` 网址、官方 LINE `@583gyplg`、合规红线）。`config/knowledge_base.yaml` 有更完整话术库，但**当前未被运行时加载**。

---

## 四、物理架构与文件结构

```
云服务器
├── Docker 容器 openclaw-adb-bridge（network_mode: host，端口 8899）
│   └── services/adb-bridge/  （main.py + 四层 + adb 二进制）
│        └── ADB ──── 15 台云手机（台湾 IP，LINE App）
│
└── 宿主机
    ├── crontab           （8:00–19:40 调度 15 个 daily_add 脚本 + 9:30 日报）
    ├── scripts/          （shell 入口 + 日报 + 工具）
    ├── config/           （devices.yaml / greetings.txt / knowledge_base.yaml / settings.yaml）
    ├── data/             （bridge.db、targets/、state/、leads/）
    ├── logs/、reports/
    └── /root/targets_all.txt  （名单，8419 行）
```

```
/root/line-crm/
├── services/adb-bridge/          ← 核心（Docker 内，端口 8899）
│   ├── main.py                   ← 业务层 + Flask API
│   ├── scheduler.py              ← 调度层
│   ├── account_manager.py        ← 状态层（SQLite）
│   ├── adb_operator.py           ← ADB 层 + DeepSeek
│   ├── db.py / logger.py         ← 持久化 / 日志
│   ├── test_all.py               ← 单元测试（mock ADB + 临时库）
│   ├── Dockerfile / requirements.txt
│   └── adb / adb-host / adb-libs* ← 静态打包的 ADB 二进制
├── scripts/
│   ├── template.sh               ← 加好友流程模板（冷却/判死由 bridge 判定，脚本只按 error 停/跳，见 §五）
│   ├── daily_add_cloud-01..15.sh ← 每台一个入口（cron 实际调用）
│   ├── batch_add.py              ← 手动批量加好友
│   ├── inter_chat.py             ← 账号互聊（模拟真人活跃）
│   ├── report.sh / push_report.sh← 日报生成 / LINE 推送
│   └── scheduler.sh / launch.sh / run_all.sh / split_targets.sh ← 旧方案（见 §七）
├── worker/                       ← 旧版 UI 层，无引用（dead code）
├── config/
│   ├── devices.yaml              ← 15 台设备 ADB 地址
│   ├── greetings.txt             ← 8 条加好友首发文案（运行时会读）
│   ├── knowledge_base.yaml       ← 话术/方案/7步流程（未在运行时加载）
│   ├── settings.yaml             ← ⚠️ 含明文 API Key 且已 git 追踪
│   └── bridge_token
├── data/
│   ├── bridge.db (+WAL/SHM)      ← SQLite 主库
│   ├── targets/                  ← 名单分段（旧，见 cloud_segment_*）
│   ├── state/                    ← 进度/冷却/线索等状态文件
│   └── leads/                    ← 意向客户线索
├── reports/daily.txt             ← 日报
├── logs/
├── docker-compose.yml            ← adb-bridge + openclaw-core + caddy
├── Makefile / requirements.txt
└── *.md 文档
```

### 数据库表

| 表 | 来源 | 用途 |
|---|---|---|
| `account_status` | `account_manager.py` | 账号状态机、每日统计、冷却、风险分 |
| `device_status` | `db.py` | 设备上下线历史 |
| `task_log` | `db.py` | 加好友/回复/发消息任务日志 |
| `reply_log` | `db.py` | AI 回复内容（分析转化率） |

---

## 五、冷却机制（已收敛为单一系统）

搜索上限触发 **5 天冷却 + 累计 3 次判死**，全部由 Python 层 `AccountManager` 统一管理，存 `data/bridge.db`（旧的 `device_limit_status.json` 已废弃）。

### 状态机

```
搜索达上限 → search_limit_count + 1
    ├─ count < 3（search_limit_max=3）→ status=cooldown，冷却 5 天（search_cooldown_days=5）
    └─ count ≥ 3 → status=dead（判死，需换号），永久排除
```

- **字段**：`search_limit_count`（已累计次数）、`search_limit_max`（默认 3）、`search_cooldown_days`（默认 5）、`cooldown_until`、`cooldown_reason`
- **冷却期满自动恢复**：`check_blocked()` 发现 `cooldown_until` 已过期 → 调 `exit_cooldown()` 恢复 `active`；`search_limit_count` **不清零**（累计）
- **判死后**：`status=dead`，被加好友端点永久拒绝，需人工换号

### 关键保障

1. **加好友端点守卫**：`/line/add-friend-by-id` 开头调 `am.check_blocked()`，冷却/判死直接返回 `error=cooldown/dead`，不碰 ADB（防止冷却期内继续搜索、误累计次数）
2. **`/health` 不再覆盖语义状态**：改用 `am.update_connectivity()`，只在非冷却/死/封等状态时才写 online/offline
3. **Shell 脚本配合**：`daily_add_cloud-XX.sh` 收到 `error=dead/cooldown/search_limit` 就 break，不再自己维护冷却 JSON

### 手动恢复一个判死的设备（换号后）

```bash
docker exec openclaw-adb-bridge python3 -c "import sqlite3;c=sqlite3.connect('/app/data/bridge.db');c.execute(\"UPDATE account_status SET status='active', cooldown_until=NULL, cooldown_reason=NULL, search_limit_count=0 WHERE device_id='cloud-XX'\");c.commit()"
```

---

## 六、关键 API 端点

所有接口运行在 `http://127.0.0.1:8899`。

| 端点 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查（默认并发检查全部设备） |
| `/devices` | GET | 列出所有已注册设备及连接状态 |
| `/device/register` | POST | 注册新设备（运行时，不需重启） |
| `/device/<id>/remove` | DELETE | 移除设备 |
| `/device/info` | GET | 设备型号/系统/电量 |
| `/line/add-friend-by-id` | POST | 通过 LINE ID 加好友并发问候 |
| `/line/check-latest-chat` | POST | 扫描未读并 AI 回复 |
| `/line/check-inbox` | POST | 检查收件箱（只读） |
| `/line/send-message` | POST | 给指定聊天发消息 |
| `/line/open` / `/line/close` | POST | 打开/关闭 LINE |
| `/line/screenshot` | GET | 截屏（base64） |
| `/line/tap` / `/line/swipe` / `/line/type` / `/line/send-key` | POST | 底层 UI 操作 |
| `/stats` | GET | 任务统计 + 近期线索 |

### 示例

```bash
# 注册设备
curl -X POST http://127.0.0.1:8899/device/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"cloud-16","addr":"IP:端口","type":"cloud","label":"云手机16"}'

# 加好友
curl -X POST "http://127.0.0.1:8899/line/add-friend-by-id?device=cloud-03" \
  -H "Content-Type: application/json" \
  -d '{"line_id":"目标ID","message":"问候语"}'

# 检查消息（需 AUTO_REPLY_ENABLED=true 时才由 loop 自动跑）
curl -X POST "http://127.0.0.1:8899/line/check-latest-chat?device=cloud-03"
```

---

## 七、扩至 100 台注意事项

1. **服务器性能**：当前 1 台管 15 台，100 台约需 7 台（每台管 15，见 `EQUIPMENT.md`）
2. **串行巡检压力**：`_auto_reply_loop` 是串行，100 台需并行化/分组
3. **名单消耗**：100 台 × 20 人 = 2000 人/天，需持续补充
4. **IP 质量**：每台需独立台湾住宅 IP，成本是主要开销
5. **新增步骤**：注册设备（`/device/register`）→ 加 `daily_add_cloud-XX.sh` → 加 cron → 在 `devices.yaml` 加条目 → 重建 Docker

---

## 八、日常维护命令

```bash
# 看加好友日志
tail -f /root/line-crm/logs/daily_add.log

# 看意向客户
cat /root/line-crm/data/leads/leads.jsonl

# 看账号状态（DB）
docker exec openclaw-adb-bridge python3 -c "import sqlite3;c=sqlite3.connect('/app/data/bridge.db');c.row_factory=sqlite3.Row;[print(dict(r)) for r in c.execute('SELECT device_id,status,daily_success,daily_add_limit,daily_search,risk_score FROM account_status ORDER BY device_id')]"

# 看设备连接状态
curl -s http://127.0.0.1:8899/devices | python3 -m json.tool

# 看名单剩余
echo "总数: $(grep -vc '^#' /root/targets_all.txt)"
echo "已处理: $(cat /root/line-crm/data/state/targets_position_shared)"

# 重建 Docker（改 main.py 等代码后）
cd /root/line-crm && docker compose build adb-bridge && docker compose up -d adb-bridge

# 开启自动回复（临时，重启后需重新设环境变量）
docker stop openclaw-adb-bridge
docker run ... -e AUTO_REPLY_ENABLED=true ...   # 或改 docker-compose.yml 后 up -d
```

---

## 九、已知问题与偏差

1. **文档/配置分歧**：多处仍引用旧路径（`/workspace/openclaw`、`/tmp/targets_clean.txt`、5/10 台）；本文档已校正为现状，其它 `.md` 以本文档为准。
2. **明文密钥入库**：`config/settings.yaml` 含 `deepseek.api_key`，且被 git 追踪；`config/bridge_token`、`.env` 也在追踪中。**建议轮换 key 并纳入 `.gitignore`**。
3. **重复实现**：`adb_operator.py:334 add_friend_by_id()` 与 `main.py:269` 端点逻辑几乎重复，前者是无人调用的死代码。
4. **冷却已收敛**：见 §五。加好友路径用 `check_blocked()`（不查 `daily_add_limit`），所以 `daily_add_limit=10` 目前只影响 `Scheduler.can_execute()`，与 shell 的 `DAILY_GOAL=20` 不一致但暂不影响加好友。
5. **`daily_add_limit` 与 `DAILY_GOAL` 仍不一致**（10 vs 20），建议后续统一为 20。
6. **占位 token**：`main.py` 钉钉推送 `YOUR_TOKEN` 未替换，推送实际不生效。
7. **`worker/` 与旧脚本**：`worker/`、`scheduler.sh`、`launch.sh`、`split_targets.sh`、`settings.yaml` 的 AI/storage 段均未被当前流程使用。
