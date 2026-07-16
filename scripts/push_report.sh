#!/bin/bash
# 生成日报并推送到 LINE

REPORT_BRIDGE="http://127.0.0.1:8899"
REPORT_DEVICE="cloud-01"
REPORT_CHAT="dgxycu2840t050"

# 生成报告文本
LOG="/root/line-crm/logs/daily_add.log"
TODAY_START=$(grep -n "$(date +%b\ %d)" "$LOG" | head -1 | cut -d: -f1)
if [ -z "$TODAY_START" ]; then
    echo "今天无记录"; exit 0
fi
TODAY_LOG=$(tail -n +$TODAY_START "$LOG")

MSG="=== $(date +%m/%d) LINE获客日报 ==="
MSG="$MSG"$'\n'
for dev in cloud-01 cloud-04 cloud-05 cloud-06 cloud-07 cloud-08 cloud-09 cloud-10; do
    dev_log=$(echo "$TODAY_LOG" | grep "$dev")
    [ -z "$dev_log" ] && continue
    ok=$(echo "$dev_log" | grep -c "✅.*$dev")
    nf=$(echo "$dev_log" | grep -c "⏭.*$dev")
    fl=$(echo "$dev_log" | grep -c "❌.*$dev")
    if echo "$dev_log" | grep -q "搜索次数达上限"; then st="🛑"
    elif echo "$dev_log" | grep -q "连续失败.*停止"; then st="❌"
    elif echo "$dev_log" | grep -q "完成：成功"; then st="✅"
    else st="⏳"; fi
    MSG="$MSG"$'\n'"$st $dev ${ok}/10 (搜不到${nf})"
done
total_ok=$(echo "$TODAY_LOG" | grep -c "✅.*confirm_add")
pos=$(cat /root/line-crm/data/state/targets_position_shared 2>/dev/null || echo "?")
MSG="$MSG"$'\n'"────────"
MSG="$MSG"$'\n'"新增${total_ok}人 进度${pos}/3020"

# 推送到LINE
curl -s -X POST "$REPORT_BRIDGE/line/send-message?device=$REPORT_DEVICE" \
  -H "Content-Type: application/json" \
  -d "{\"chat_name\":\"$REPORT_CHAT\",\"text\":\"$MSG\"}" --max-time 90 > /dev/null 2>&1

echo "已推送: $MSG"
