# 部署指南

## 前提
- 新服务器能访问云手机 ADB 端口
- 已安装 Docker

## 部署步骤

### 1. 拷贝项目
```bash
scp -r /root/line-crm user@新服务器:/root/
```

### 2. 构建 Bridge
```bash
cd /root/line-crm && docker build -t openclaw-adb-bridge:local .
```

### 3. 启动
```bash
docker run -d --name openclaw-adb-bridge \
  --restart=always \
  --network host \
  -v /root/line-crm/data/devices.json:/app/data/devices.json \
  openclaw-adb-bridge:local

### 4. 设置定时
```bash
crontab -e
# 加入: 3 8 * * * make -C /root/line-crm start
# 加入: 30 9 * * * bash /root/line-crm/scripts/report.sh

### 5. 验证
```bash
make -C /root/line-crm health
```
