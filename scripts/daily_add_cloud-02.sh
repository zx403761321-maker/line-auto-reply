#!/bin/bash
# 每天9点：顺着名单继续，直到当天满20个成功
TARGETS="/tmp/targets_clean.txt"
BRIDGE="http://127.0.0.1:8899"
DEVICE="cloud-02"
DAILY_GOAL=20
LOG="/root/line-crm/logs/daily_add.log"
POSFILE="/root/line-crm/data/state/targets_position_shared"

# 读取上次位置，没有则从0开始
pos=$(cat "$POSFILE" 2>/dev/null || echo 0)
all_ids=$(grep -v '^#' $TARGETS | tr -d '"' | grep -v '^$')

echo "[$(date)] 开始，位置$pos，目标${DAILY_GOAL}人" >> $LOG

success=0
attempt=0
idx=0

for id in $all_ids; do
    [ -z "$id" ] && continue
    # 跳过已处理过的
    if [ $idx -lt $pos ]; then
        idx=$((idx+1))
        continue
    fi
    idx=$((idx+1))
    attempt=$((attempt+1))

    echo "[$(date +%m-%d\ %H:%M)] [$DEVICE] #${idx} $id" >> $LOG
    result=$(curl -s -X POST "$BRIDGE/line/add-friend-by-id?device=$DEVICE" \
      -H "Content-Type: application/json" \
      -d "{\"line_id\":\"$id\",\"message\":\"你好～我是貸款顧問雅琳，有資金需求都可以問我喔 😊\"}" \
      --max-time 90)
    ok=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok'))" 2>/dev/null)
    steps=$(echo "$result" | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin).get('steps',[])[-3:]))" 2>/dev/null)

    if [ "$ok" = "True" ]; then
        success=$((success+1))
        echo "  ✅ [$DEVICE] ($success/$DAILY_GOAL) $steps" >> $LOG
    else
        echo "  ❌ 跳过" >> $LOG
    fi

    # 保存当前位置
    echo $idx > "$POSFILE"

    [ $success -ge $DAILY_GOAL ] && break
    sleep 90
done

echo "[$(date)] 完成：成功${success}，位置${idx}" >> $LOG
