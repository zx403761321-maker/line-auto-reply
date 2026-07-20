#!/bin/bash
LOG="/root/line-crm/logs/daily_add.log"
# 2,3,9号正常
DEVICES=(cloud-02 cloud-03 cloud-09)

for dev in "${DEVICES[@]}"; do
    echo "[$(date +%H:%M)] 启动 $dev" >> $LOG
    bash "/root/line-crm/scripts/daily_add_${dev}.sh"
    echo "[$(date +%H:%M)] $dev 完成" >> $LOG
done
echo "[$(date +%H:%M)] 全部完成" >> $LOG
