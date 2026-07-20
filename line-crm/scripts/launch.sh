#!/bin/bash
# 生产桥 8899
PROD_DEVICES="cloud-01 cloud-04 cloud-06 cloud-07 cloud-08 cloud-09 cloud-10"
# 测试桥 8898 (坐标适配)
TEST_DEVICES=""

for dev in $PROD_DEVICES; do
    nohup bash /root/line-crm/scripts/template.sh "$dev" > /dev/null 2>&1 &
    echo "[$(date)] 已启动 $dev (生产桥)"
done
for dev in $TEST_DEVICES; do
    BRIDGE="http://127.0.0.1:8898" nohup bash /root/line-crm/scripts/template.sh "$dev" > /dev/null 2>&1 &
    echo "[$(date)] 已启动 $dev (测试桥)"
done
