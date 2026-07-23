#!/bin/bash
# 总调度：9:00-11:00串行添加，其余时间回复循环
# 避免同设备并发冲突

LOG="/root/line-crm/logs/scheduler.log"

while true; do
    HOUR=$(date +%H)

    if [ $HOUR -ge 9 ] && [ $HOUR -lt 11 ]; then
        echo "[$(date)] 添加窗口，逐台跑" >> $LOG
        for dev in cloud-01 cloud-02 cloud-03 cloud-04 cloud-05; do
            echo "[$(date)] $dev 开始" >> $LOG
            /root/line-crm/scripts/daily_add_${dev}.sh 2>&1 >> $LOG
            sleep 10  # 间隔10秒
        done
        echo "[$(date)] 添加完成，休眠到明天" >> $LOG
        sleep 3600  # 睡1小时后再检查
    else
        # 非添加时段：保持回复循环在跑
        sleep 300
    fi
done
