# LINE 获客系统 — 部署指南

## 系统架构

```
Docker 容器 (openclaw-adb-bridge)
├── main.py          HTTP → ADB → 控制云手机
├── scheduler.py     定时器 (8:00启动, 9:30日报)
├── devices.json     云手机IP映射
└── 端口 8899
```

## 部署步骤

### 1. 拷贝项目到新服务器

```bash
scp -r /root/line-crm user@新服务器IP:/root/
```

### 2. 准备 ADB 环境

新服务器需要能连通云手机的 ADB 端口。安装 ADB：

```bash
apt install -y adb
```

验证连通：

```bash
adb connect 云手机IP:端口
adb -s 云手机IP:端口 shell echo ok   # 应返回 ok
```

### 3. 改配置

**云手机地址：** 编辑 `data/devices.json`，填入新的 ADB 地址

```json
{
  "cloud-01": {"addr": "新IP:端口", "type": "cloud", "label": "云手机-01"},
  "cloud-02": {"addr": "新IP:端口", "type": "cloud", "label": "云手机-02"}
}
```

**启动哪些设备：** 编辑 `scripts/launch.sh`，改 `PROD_DEVICES` 列表

**名单：** 替换 `data/targets/targets_all.txt`，一行一个 LINE ID

### 4. 构建并启动

```bash
cd /root/line-crm
make build
docker run -d --name openclaw-adb-bridge \
  --restart=always --network host \
  -v /root/line-crm/data:/app/data \
  -v /root/line-crm/logs:/root/line-crm/logs \
  -v /root/line-crm/scripts:/root/line-crm/scripts \
  openclaw-adb-bridge:local
```

定时器（8:00 自动加好友、9:30 日报）已内置，无需额外配置。

### 5. 日常操作

```bash
make report    # 查看今日日报
make status    # 查看运行状态
make health    # 检查所有设备连通性
make logs      # 实时日志
```

日报文件：`reports/daily.txt`（每天追加）

### 新增云手机

在 `data/devices.json` 加一条记录，在 `scripts/launch.sh` 的 `PROD_DEVICES` 里加上设备名，重启桥即可。
