#!/bin/bash
sleep $((RANDOM % 30))
TARGETS="/root/line-crm/data/targets/targets_all.txt"
BRIDGE="${BRIDGE:-http://127.0.0.1:8899}"
GREETING_FILE="/root/line-crm/config/greetings.txt"
DEVICE="$1"
DAILY_GOAL=10
LOG="/root/line-crm/logs/daily_add.log"
POSFILE="/root/line-crm/data/state/targets_position_shared"
LOCKFILE="/tmp/add_position.lock"
MAX_FAIL=10
LIMIT_FILE="/root/line-crm/data/state/device_limit_status.json"
ALERT_FILE="/root/line-crm/data/state/alerts.txt"
COOLDOWN_DAYS=2
MAX_COOLDOWNS=3

# ─── 检查设备是否在冷却期 ───
check_cooldown() {
    if [ ! -f "$LIMIT_FILE" ]; then return 0; fi
    local status cooldown_until cooldown_count
    status=$(python3 -c "
import json, sys
try:
    with open('$LIMIT_FILE') as f: d = json.load(f)
    dev = d.get('$DEVICE', {})
    print(dev.get('status', 'active'))
except: print('active')
" 2>/dev/null)
    cooldown_until=$(python3 -c "
import json
try:
    with open('$LIMIT_FILE') as f: d = json.load(f)
    dev = d.get('$DEVICE', {})
    print(dev.get('cooldown_until', ''))
except: print('')
" 2>/dev/null)
    cooldown_count=$(python3 -c "
import json
try:
    with open('$LIMIT_FILE') as f: d = json.load(f)
    dev = d.get('$DEVICE', {})
    print(dev.get('cooldown_count', 0))
except: print(0)
" 2>/dev/null)

    if [ "$status" = "needs_replacement" ]; then
        echo "[$(date)] [$DEVICE] ⛔ 已冷却${cooldown_count}次仍上限，需换号，跳过" >> $LOG
        return 1
    fi
    if [ "$status" = "cooling_down" ]; then
        local now_ts until_ts
        now_ts=$(date +%s)
        until_ts=$(date -d "$cooldown_until" +%s 2>/dev/null || echo 0)
        if [ "$now_ts" -lt "$until_ts" ]; then
            echo "[$(date)] [$DEVICE] 🧊 冷却中，${cooldown_until} 后重试 (第${cooldown_count}次)" >> $LOG
            return 1
        fi
        echo "[$(date)] [$DEVICE] 🔥 冷却期满，第${cooldown_count}次重试" >> $LOG
    fi
    return 0
}

# ─── 标记设备达上限 ───
mark_search_limit() {
    local retry_after_cooldown="$1"  # "yes" 表示冷却后重试又秒毙
    python3 -c "
import json, os
from datetime import datetime, timedelta

cooldown_days = $COOLDOWN_DAYS
max_cooldowns = $MAX_COOLDOWNS
now = datetime.now()
cooldown_until = (now + timedelta(days=cooldown_days)).strftime('%Y-%m-%d')

data = {}
if os.path.exists('$LIMIT_FILE'):
    with open('$LIMIT_FILE') as f:
        try: data = json.load(f)
        except: pass

dev = data.get('$DEVICE', {})
prev_count = dev.get('cooldown_count', 0)
count = prev_count + 1

# 冷却后重试第一把就上限 → 不额外消耗次数，直接延冷却
if '$retry_after_cooldown' == 'yes':
    count = prev_count  # 不额外计数，当上次冷却无效

if count >= max_cooldowns:
    dev = {'status': 'needs_replacement', 'cooldown_count': count,
           'cooldown_until': cooldown_until, 'reason': '搜索次数达上限',
           'updated': now.isoformat()}
else:
    dev = {'status': 'cooling_down', 'cooldown_count': count,
           'cooldown_until': cooldown_until, 'reason': '搜索次数达上限',
           'updated': now.isoformat()}

data['$DEVICE'] = dev
with open('$LIMIT_FILE', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

# 写提醒
alert_msg = ''
if count >= max_cooldowns:
    alert_msg = f'[{now}] $DEVICE → needs_replacement，已冷却{count}次仍搜索上限，建议换号'
else:
    alert_msg = f'[{now}] $DEVICE → cooling_down({count}/{max_cooldowns})，下次重试 {cooldown_until}'

with open('$ALERT_FILE', 'a') as f:
    f.write(alert_msg + '\n')
"
}

echo "[$(date)] [$DEVICE] 开始，目标${DAILY_GOAL}人" >> $LOG

# 冷却检查
check_cooldown || exit 0

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
    # 桥健康检查，挂了自动重启
    if ! curl -s --max-time 3 http://127.0.0.1:8899/health > /dev/null 2>&1; then
        echo "  🩺 [$DEVICE] 桥无响应，重启..." >> $LOG
        docker restart openclaw-adb-bridge 2>/dev/null
        sleep 15
    fi
        result=$(curl -s -X POST "$BRIDGE/line/add-friend-by-id?device=$DEVICE" \
          -H "Content-Type: application/json" \
          -d "{\"line_id\":\"$id\",\"message\":\"$GREETING\"}" --max-time 90)
        ok=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok'))" 2>/dev/null)
        steps=$(echo "$result" | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin).get('steps',[])[-3:]))" 2>/dev/null)
        error=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)

        # bridge正常响应就跳出重试循环
        [ -n "$ok" ] && break
        retry=$((retry+1))
        echo "  🔄 [$DEVICE] bridge无响应，重试${retry}/3..." >> $LOG
        sleep 10
    done

    if [ "$ok" = "True" ]; then
        success=$((success+1)); consec_fail=0
        echo "  ✅ [$DEVICE] ($success/$DAILY_GOAL) $steps" >> $LOG
    elif [ "$error" = "search_limit" ]; then
        echo "[$(date +%H:%M)] [$DEVICE] 🛑 搜索次数达上限，进入冷却" >> $LOG
        # 判断是否冷却后重试秒毙
        was_cooled=$(python3 -c "
import json
try:
    with open('$LIMIT_FILE') as f: d = json.load(f)
    c = d.get('$DEVICE', {}).get('cooldown_count', 0)
    print(c)
except: print(0)
" 2>/dev/null)
        if [ "$was_cooled" -gt 0 ] && [ "$success" -eq 0 ]; then
            mark_search_limit "yes"
        else
            mark_search_limit "no"
        fi
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
