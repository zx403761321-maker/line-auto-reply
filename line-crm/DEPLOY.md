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

### 1. 导出镜像

```bash
docker save openclaw-adb-bridge:local -o /tmp/bridge.tar
```

### 2. 拷贝到新服务器

```bash
# 镜像
scp /tmp/bridge.tar root@新服务器IP:/tmp/

# 配置
scp -r /root/line-crm/data root@新服务器IP:/root/line-crm/
scp -r /root/line-crm/config root@新服务器IP:/root/line-crm/
scp -r /root/line-crm/scripts root@新服务器IP:/root/line-crm/
```

### 3. 新服务器上启动

```bash
# 导入镜像
docker load -i /tmp/bridge.tar

# 装 ADB
apt install -y adb

# 启动（一行，定时器已内置）
docker run -d --name openclaw-adb-bridge \
  --restart=always --network host \
  -v /root/line-crm/data:/app/data \
  -v /root/line-crm/data:/root/line-crm/data \
  -v /root/line-crm/logs:/root/line-crm/logs \
  -v /root/line-crm/scripts:/root/line-crm/scripts \
  openclaw-adb-bridge:local
```

不需要 crontab，不需要 make，不需要构建。Docker 自带一切。

### 4. 改配置（启动前改好）

编辑 `/root/line-crm/data/devices.json` → 云手机 ADB 地址

编辑 `/root/line-crm/scripts/launch.sh` → `PROD_DEVICES` 列表

替换 `/root/line-crm/data/targets/targets_all.txt` → 名单

### 5. 验证

```bash
docker logs openclaw-adb-bridge --tail 20
curl http://localhost:8899/health
```

### 后续

- 日报：`reports/daily.txt`
- 日志：`docker logs openclaw-adb-bridge`
- 加新手机：改 `devices.json` + `launch.sh`，`docker restart openclaw-adb-bridge` 即可
