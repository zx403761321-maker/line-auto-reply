#!/bin/bash
LOG="/root/line-crm/logs/daily_add.log"
REPORT_FILE="/root/line-crm/reports/daily.txt"
mkdir -p /root/line-crm/reports

# 只处理今天的日志：从今天第一条记录开始
TODAY_START=$(grep -n "$(date +%b\ %d)" "$LOG" | head -1 | cut -d: -f1)
if [ -z "$TODAY_START" ]; then
    echo "今天还没有运行记录"
    exit 0
fi
TODAY_LOG=$(tail -n +$TODAY_START "$LOG")

echo "=== $(date +%Y-%m-%d) LINE获客日报 ==="
echo ""
printf "%-12s %6s %6s %6s %s\n" "设备" "成功" "搜不到" "失败" "状态"
printf "%-12s %6s %6s %6s %s\n" "────" "────" "────" "────" "────"

total_ok=0; total_nf=0; total_fail=0; count=0

for dev in cloud-01 cloud-04 cloud-05 cloud-06 cloud-07 cloud-08 cloud-09 cloud-10; do
    dev_log=$(echo "$TODAY_LOG" | grep "$dev")
    [ -z "$dev_log" ] && continue
    count=$((count + 1))

    ok=$(echo "$dev_log" | grep -c "✅.*$dev")
    nf=$(echo "$dev_log" | grep -c "⏭.*$dev")
    fl=$(echo "$dev_log" | grep -c "❌.*$dev")
    total_ok=$((total_ok + ok)); total_nf=$((total_nf + nf)); total_fail=$((total_fail + fl))

    if echo "$dev_log" | grep -q "搜索次数达上限"; then
        st="🛑 搜索上限"
    elif echo "$dev_log" | grep -q "连续失败.*停止"; then
        st="❌ 连败停止"
    elif echo "$dev_log" | grep -q "完成：成功"; then
        st="✅ 完成"
    else
        st="⏳ 运行中"
    fi

    printf "%-12s %6s %6s %6s %s\n" "$dev" "$ok" "$nf" "$fl" "$st"
done

echo ""
echo "─────────────────────────────"
echo "共 ${count} 台，新增 ${total_ok} 人，搜不到 ${total_nf}，失败 ${total_fail}"
pos=$(cat /root/line-crm/data/state/targets_position_shared 2>/dev/null || echo "?")
total=$(wc -l < /root/targets_all.txt)
echo "名单进度: ${pos}/${total} ($(( pos * 100 / total ))%)"

# 追加到日报
echo "" >> "$REPORT_FILE"
cat <<< "" >> /dev/null  # noop
# tee to report file
{
    echo ""; echo "=== $(date +%Y-%m-%d) LINE获客日报 ==="; echo ""
    printf "%-12s %6s %6s %6s %s\n" "设备" "成功" "搜不到" "失败" "状态"
    printf "%-12s %6s %6s %6s %s\n" "────" "────" "────" "────" "────"
    for dev in cloud-01 cloud-04 cloud-05 cloud-06 cloud-07 cloud-08 cloud-09 cloud-10; do
        dev_log=$(echo "$TODAY_LOG" | grep "$dev"); [ -z "$dev_log" ] && continue
        ok=$(echo "$dev_log" | grep -c "✅.*$dev")
        nf=$(echo "$dev_log" | grep -c "⏭.*$dev")
        fl=$(echo "$dev_log" | grep -c "❌.*$dev")
        if echo "$dev_log" | grep -q "搜索次数达上限"; then st="🛑 搜索上限"
        elif echo "$dev_log" | grep -q "连续失败.*停止"; then st="❌ 连败停止"
        elif echo "$dev_log" | grep -q "完成：成功"; then st="✅ 完成"
        else st="⏳ 运行中"; fi
        printf "%-12s %6s %6s %6s %s\n" "$dev" "$ok" "$nf" "$fl" "$st"
    done
} >> "$REPORT_FILE"
echo "已追加到 $REPORT_FILE"
