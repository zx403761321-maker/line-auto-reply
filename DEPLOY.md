# 部署指南

## 一键部署

```bash
# 1. 拷贝
scp -r /root/line-crm user@新服务器:/root/

# 2. 改配置
#    bridge/devices.json → 云手机ADB地址
#    data/targets/targets_all.txt → LINE ID名单

# 3. 构建+启动
cd /root/line-crm && make build
docker run -d --name openclaw-adb-bridge \
  --restart=always --network host \
  -v /root/line-crm/data:/app/data \
  -v /root/line-crm/logs:/root/line-crm/logs \
  -v /root/line-crm/scripts:/root/line-crm/scripts \
  -v /root/line-crm/Makefile:/root/line-crm/Makefile \
  openclaw-adb-bridge:local
```

定时已内置在 Docker 里，不需要额外设置。
