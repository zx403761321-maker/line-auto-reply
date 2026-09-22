#!/bin/bash
# 加好友流程模板 — 由 make deploy 或 sed 将 $1 替换为设备名生成 daily_add_cloud-XX.sh
# 冷却/判死逻辑由 adb-bridge 的 AccountManager 统一管理，脚本只根据返回的 error 决定停/跳。
sleep $((RANDOM % 30))
TARGETS="/root/targets_all.txt"
BRIDGE="${BRIDGE:-http://127.0.0.1:8899}"
GREETING_FILE="/root/line-crm/config/greetings.txt"
DEVICE="$1"
DAILY_GOAL=10
LOG="/root/line-crm/logs/daily_add.log"
POSFILE="/root/line-crm/data/state/targets_position_shared"
LOCKFILE="/tmp/add_position.lock"
MAX_FAIL=10

echo "[$(date)] [$DEVICE] 开始，目标${DAILY_GOAL}人" >> $LOG

success=0
consec_fail=0

while [ $success -lt $DAILY_GOAL ]; do
    exec 200>"$LOCKFILE"; flock 200
    pos=$(cat "$POSFILE" 2>/dev/null || echo 0)
    next=$((pos + 1)); echo $next > "$POSFILE"
    flock -u 200; exec 200>&-

    id=$(grep -v '^#' "$TARGETS" | tr -d '"' | grep -v '^$' | sed -n "${next}p" | cut -f1)
    [ -z "$id" ] && echo "[$(date)] [$DEVICE] 名单耗尽 pos=$next" >> $LOG && break

    GREETING=$(shuf -n1 "$GREETING_FILE")
    echo "[$(date +%m-%d\ %H:%M)] [$DEVICE] #${next} $id → ${GREETING:0:30}..." >> $LOG

    retry=0
    while [ $retry -le 3 ]; do
        result=$(curl -s -X POST "$BRIDGE/line/add-friend-by-id?device=$DEVICE" \
          -H "Content-Type: application/json" \
          -d "{\"line_id\":\"$id\",\"message\":\"$GREETING\"}" --max-time 90)
        ok=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok'))" 2>/dev/null)
        steps=$(echo "$result" | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin).get('steps',[])[-3:]))" 2>/dev/null)
        error=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)

        # bridge 正常响应就跳出重试循环
        [ -n "$ok" ] && break
        retry=$((retry+1))
        echo "  🔄 [$DEVICE] bridge无响应，重试${retry}/3..." >> $LOG
        sleep 10
    done

    if [ "$ok" = "True" ]; then
        success=$((success+1)); consec_fail=0
        echo "  ✅ [$DEVICE] ($success/$DAILY_GOAL) $steps" >> $LOG
    elif [ "$error" = "search_limit" ]; then
        echo "[$(date +%H:%M)] [$DEVICE] 🛑 搜索次数达上限，已进入冷却" >> $LOG
        break
    elif [ "$error" = "cooldown" ]; then
        echo "[$(date +%H:%M)] [$DEVICE] 🧊 冷却中，跳过今天" >> $LOG
        break
    elif [ "$error" = "dead" ]; then
        echo "[$(date +%H:%M)] [$DEVICE] ⛔ 已判死（搜索上限3次），需换号" >> $LOG
        break
    elif echo "$steps" | grep -q "no_result"; then
        echo "  ⏭ [$DEVICE] $steps (查无此人)" >> $LOG
    else
        consec_fail=$((consec_fail+1))
        echo "  ❌ [$DEVICE] $steps (连续失败${consec_fail}次)" >> $LOG
        [ $consec_fail -ge $MAX_FAIL ] && echo "[$(date +%H:%M)] [$DEVICE] 🛑 连续失败${consec_fail}次，自动停止" >> $LOG && break
    fi

    hour=$(date +%H)
    if [ "$hour" -ge 22 ] || [ "$hour" -lt 8 ]; then
        echo "[$(date +%H:%M)] [$DEVICE] 💤 休息到明早8:00" >> $LOG
        while [ "$(date +%H)" -ge 22 ] || [ "$(date +%H)" -lt 8 ]; do sleep 600; done
        echo "[$(date +%H:%M)] [$DEVICE] ☀️ 继续" >> $LOG
    fi
    sleep $((60 + RANDOM % 120))
done
echo "[$(date)] [$DEVICE] 完成：成功${success}，位置$(cat $POSFILE)" >> $LOG
