#!/bin/bash
# 将 /tmp/targets_clean.txt 均匀分成10份给10台设备，避免重复添加
# 用法: bash /root/line-crm/scripts/split_targets.sh

SOURCE="/tmp/targets_clean.txt"
DIR="/root/line-crm/data/targets"

mkdir -p "$DIR"

# 提取干净ID（去掉引号和空白）
clean_ids=$(grep -v '^#' "$SOURCE" | tr -d '"' | grep -v '^$')
total=$(echo "$clean_ids" | wc -l)

echo "总名单数: $total"
echo "分给10台设备，每台约 $((total / 10)) 人"

# 分成10个文件
echo "$clean_ids" | split -d -l $(((total + 9) / 10)) - "$DIR/cloud_segment_"

# 重命名为 cloud-01 ~ cloud-10
for i in $(seq -w 0 9); do
    seg_num=$((10#$i + 1))
    seg_num_fmt=$(printf "%02d" $seg_num)
    src="$DIR/cloud_segment_$i"
    dst="$DIR/targets_cloud-${seg_num_fmt}.txt"
    if [ -f "$src" ]; then
        mv "$src" "$dst"
        count=$(wc -l < "$dst")
        echo "  cloud-${seg_num_fmt}: $count 人 → $dst"
    fi
done

# 重置所有设备位置
for dev in 01 02 03 04 05 06 07 08 09 10; do
    echo "0" > "/root/line-crm/data/state/targets_position_cloud-${dev}"
    echo "  cloud-${dev} 位置已重置为0"
done

echo ""
echo "✅ 分段完成！每台设备现在有独立的名单位置。"
