#!/bin/bash
# 04/05/10 搜索上限暂跳过
for dev in cloud-01 cloud-06 cloud-07 cloud-08 cloud-09; do
    nohup bash /root/line-crm/scripts/daily_add_${dev}.sh > /dev/null 2>&1 &
    echo "[$(date)] 已启动 $dev"
done
