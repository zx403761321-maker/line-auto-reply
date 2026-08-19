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

# 从 devices.yaml 动态读取设备列表（每台服务器各自维护）
DEVICES=$(grep -oP 'id:\s*\Kcloud-\d+' /root/line-crm/config/devices.yaml)
[ -z "$DEVICES" ] && DEVICES="cloud-01 cloud-02 cloud-03 cloud-04 cloud-05 cloud-06 cloud-07 cloud-08 cloud-09 cloud-10 cloud-11 cloud-12 cloud-13 cloud-14 cloud-15"

for dev in $DEVICES; do
    dev_log=$(echo "$TODAY_LOG" | grep "$dev")
    [ -z "$dev_log" ] && continue
    count=$((count + 1))

    ok=$(echo "$dev_log" | grep -c "✅.*$dev")
    nf=$(echo "$dev_log" | grep -c "⏭.*$dev")
    fl=$(echo "$dev_log" | grep -c "❌.*$dev")
    total_ok=$((total_ok + ok)); total_nf=$((total_nf + nf)); total_fail=$((total_fail + fl))

    if echo "$dev_log" | grep -q "已冷却3次仍上限"; then
        st="🔁 需换号"
    elif echo "$dev_log" | grep -q "搜索次数达上限"; then
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
pos=$(cat /root/line-crm/data/state/targets_position_shared 2>/dev/null || echo "0")
total=$(wc -l < /root/line-crm/data/targets/targets_all.txt 2>/dev/null || echo "0")
if [ "$total" -gt 0 ] 2>/dev/null; then
    echo "名单进度: ${pos}/${total} ($(( pos * 100 / total ))%)"
else
    echo "名单进度: ${pos}/?"
fi

# 追加到日报文件
{
    echo ""; echo "=== $(date +%Y-%m-%d) LINE获客日报 ==="; echo ""
    printf "%-12s %6s %6s %6s %s\n" "设备" "成功" "搜不到" "失败" "状态"
    printf "%-12s %6s %6s %6s %s\n" "────" "────" "────" "────" "────"
    for dev in $DEVICES; do
        dev_log=$(echo "$TODAY_LOG" | grep "$dev"); [ -z "$dev_log" ] && continue
        ok=$(echo "$dev_log" | grep -c "✅.*$dev")
        nf=$(echo "$dev_log" | grep -c "⏭.*$dev")
        fl=$(echo "$dev_log" | grep -c "❌.*$dev")
        if echo "$dev_log" | grep -q "已冷却3次仍上限"; then st="🔁 需换号"
        elif echo "$dev_log" | grep -q "搜索次数达上限"; then st="🛑 搜索上限"
        elif echo "$dev_log" | grep -q "连续失败.*停止"; then st="❌ 连败停止"
        elif echo "$dev_log" | grep -q "完成：成功"; then st="✅ 完成"
        else st="⏳ 运行中"; fi
        printf "%-12s %6s %6s %6s %s\n" "$dev" "$ok" "$nf" "$fl" "$st"
    done
} >> "$REPORT_FILE"
echo "已追加到 $REPORT_FILE"
