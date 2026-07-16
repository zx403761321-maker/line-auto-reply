# LINE 获客系统 — 运维手册

## 系统简介

每天自动搜索 LINE ID、添加好友、发送打招呼消息。多台云手机同时运行，统一分配任务，互不冲突。

**核心流程：** 定时启动 → 每台手机轮流抢任务 → 搜ID → 添加 → 发问候 → 继续下一个

## 日常操作

### 每天要做的事

**早上 9:30 查看日报**

```bash
cd /root/line-crm && make report
```

或者直接打开报表文件：`reports/daily.txt`

日报示例：
```
=== 07/16 LINE获客日报 ===
设备       成功 搜不到 失败 状态
cloud-01     10      1     0 ✅ 完成
cloud-06     10      3     0 ✅ 完成
─────────────
新增 50 人，进度 2087/3020 (69%)
```

### 查看实时状态

```bash
make status    # 显示运行中设备、桥状态、当前进度
make logs      # 实时日志（Ctrl+C 退出）
make health    # 检查所有云手机连通性
```

### 手动启动/停止

```bash
make start     # 启动所有设备
make stop      # 停止所有设备
```

## 配置修改

### 新加云手机

1. 编辑 `data/devices.json`，加一条：
```json
"cloud-11": {"addr": "新IP:端口", "type": "cloud", "label": "新手机"}
```
2. 编辑 `scripts/launch.sh`，在 `PROD_DEVICES` 里加上 `cloud-11`
3. 重启桥：`docker restart openclaw-adb-bridge`

### 更换名单

替换 `data/targets/targets_all.txt`，一行一个 LINE ID。不要放纯数字手机号（命中率低）。

### 修改问候语

编辑 `config/greetings.txt`，每行一条文案。网址 `dorrj.com` 需要保留。

### 重置进度

```bash
make clean                 # 清空进度，从头开始
```

## 常见问题

### 某台设备 0 成功

```bash
make health               # 查ADB连通性
docker restart openclaw-adb-bridge  # 重启桥重试
```

如果设备持续失败，可能是搜索达上限（当天不再搜）或 LINE 被限制。

### 搜索次数达上限

系统会自动停止该设备。过了当天自动恢复，第二天会重新跑。

### 系统完全没跑

检查 Docker 是否在运行：
```bash
docker ps | grep bridge
```

如果没有，启动它：
```bash
docker start openclaw-adb-bridge
```

### 桥挂了

Docker 已设置 `--restart=always`，会自动重启。系统也会在每次请求前检查桥状态。

## 关键目录

| 路径 | 用途 |
|------|------|
| `logs/daily_add.log` | 运行日志 |
| `reports/daily.txt` | 日报汇总 |
| `data/state/targets_position_shared` | 名单进度 |
| `data/devices.json` | 云手机地址 |
| `config/greetings.txt` | 问候语文案 |

## 技术支持

系统用 Git 管理，出错可以回滚：
```bash
git -C /root/line-crm log --oneline    # 看历史
git -C /root/line-crm checkout -- .    # 恢复到上一次存档
```
