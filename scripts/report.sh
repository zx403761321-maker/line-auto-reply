#!/bin/bash
# LINE 获客日报生成器
# 用法: ./report.sh              → 生成昨天的日报（默认）
#       ./report.sh today        → 生成今天的日报
#       ./report.sh 2026-07-29   → 生成指定日期的日报

LOG="/root/line-crm/logs/daily_add.log"
REPORT_DIR="/root/line-crm/reports"
mkdir -p "$REPORT_DIR"

# ─── 确定目标日期 ───
if [ -n "$1" ]; then
    case "$1" in
        today)  TARGET_DATE=$(date +%Y-%m-%d) ;;
        *)      TARGET_DATE="$1" ;;  # 直接传日期如 2026-07-29
    esac
else
    TARGET_DATE=$(date -d "yesterday" +%Y-%m-%d)
fi

# 转为日志里的格式: "Jul 29", "Aug  1" 等 (日志用 %e 空格补齐单位数日期)
LOG_DATE=$(date -d "$TARGET_DATE" "+%b %e")
REPORT_FILE="$REPORT_DIR/daily.txt"

# ─── 提取目标日期的日志 ───
TODAY_START=$(grep -n "$LOG_DATE" "$LOG" | head -1 | cut -d: -f1)
if [ -z "$TODAY_START" ]; then
    echo "${TARGET_DATE} 没有运行记录"
    exit 0
fi

# 找到下一天的起始行，只取目标日期范围内的日志
NEXT_DAY=$(date -d "$TARGET_DATE +1 day" "+%b %e")
NEXT_START=$(grep -n "$NEXT_DAY" "$LOG" | head -1 | cut -d: -f1)

if [ -n "$NEXT_START" ] && [ "$NEXT_START" -gt "$TODAY_START" ]; then
    DAY_LOG=$(sed -n "${TODAY_START},$((NEXT_START - 1))p" "$LOG")
else
    DAY_LOG=$(tail -n +$TODAY_START "$LOG")
fi

# 过滤掉混入日志里的历史日报汇总行，避免误计设备/状态
DAY_LOG=$(echo "$DAY_LOG" | grep -vE '^(=== |设备|────|共 |新增好友:|未找到:|失败:|名单进度:|────────────────|cloud-[0-9]+ +[0-9]+)')

# ─── 生成日报 ───
{
    echo ""
    echo "=== ${TARGET_DATE} LINE获客日报 ==="
    echo ""
    printf "%-12s %6s %6s %6s %-12s %s\n" "设备" "成功" "搜不到" "失败" "耗时" "状态"
    printf "%-12s %6s %6s %6s %-12s %s\n" "────" "────" "────" "────" "────────" "────"
} | tee -a "$REPORT_FILE"

total_ok=0; total_nf=0; total_fail=0; count=0

for dev in cloud-{01,02,03,04,05,06,07,08,09,10,11,12,13,14,15}; do
    dev_log=$(echo "$DAY_LOG" | grep "$dev")
    [ -z "$dev_log" ] && continue
    count=$((count + 1))

    ok=$(echo "$dev_log" | grep -c "✅.*$dev")
    nf=$(echo "$dev_log" | grep -c "⏭.*$dev")
    fl=$(echo "$dev_log" | grep -c "❌.*$dev")
    total_ok=$((total_ok + ok)); total_nf=$((total_nf + nf)); total_fail=$((total_fail + fl))

    # 状态判断
    if echo "$dev_log" | grep -q "搜索次数达上限"; then
        st="🛑 搜索上限"
    elif echo "$dev_log" | grep -q "连续失败.*停止"; then
        st="❌ 连败停止"
    elif echo "$dev_log" | grep -q "完成：成功"; then
        st="✅ 完成"
    else
        st="⏳ 未完成"
    fi

    # 计算耗时
    start_ts=$(echo "$dev_log" | grep "开始" | head -1 | sed 's/\[//;s/\]//' | awk '{print $1,$2,$3,$4}')
    end_ts=$(echo "$dev_log" | grep "完成" | head -1 | sed 's/\[//;s/\]//' | awk '{print $1,$2,$3,$4}')
    if [ -n "$start_ts" ] && [ -n "$end_ts" ]; then
        start_ep=$(date -d "$start_ts" +%s 2>/dev/null)
        end_ep=$(date -d "$end_ts" +%s 2>/dev/null)
        if [ -n "$start_ep" ] && [ -n "$end_ep" ] && [ "$end_ep" -gt "$start_ep" ]; then
            diff=$((end_ep - start_ep))
            duration="${diff}秒"
            [ "$diff" -ge 60 ] && duration="$((diff/60))分$((diff%60))秒"
            [ "$diff" -ge 3600 ] && duration="$((diff/3600))时$(((diff%3600)/60))分"
        else
            duration="-"
        fi
    else
        duration="-"
    fi

    printf "%-12s %6s %6s %6s %-12s %s\n" "$dev" "$ok" "$nf" "$fl" "$duration" "$st" | tee -a "$REPORT_FILE"
done

# ─── 汇总 ───
total_targets=$(wc -l < /root/targets_all.txt)
{
    echo ""
    echo "─────────────────────────────"
    echo "共 ${count} 台设备运行"
    echo "新增好友: ${total_ok}"
    echo "未找到:   ${total_nf}"
    echo "失败:     ${total_fail}"
    echo "名单进度: $(cat /root/line-crm/data/state/targets_position_shared 2>/dev/null || echo '?')/${total_targets}"
} | tee -a "$REPORT_FILE"

echo ""
echo "日报已追加到: $REPORT_FILE"
