#!/bin/bash
# 在每台机器上运行一次，保护本地独有文件不被 git 覆盖
# 用法: bash scripts/setup-local-protection.sh
set -e

echo "🔒 保护本地文件..."

git update-index --skip-worktree config/settings.yaml
git update-index --skip-worktree data/leads/leads.jsonl
git update-index --skip-worktree data/leads/loan_leads.txt
git update-index --skip-worktree data/leads/贷款意向线索.txt
git update-index --skip-worktree data/targets/cloud_segment_00
git update-index --skip-worktree data/targets/cloud_segment_01
git update-index --skip-worktree data/targets/cloud_segment_02
git update-index --skip-worktree data/targets/cloud_segment_03
git update-index --skip-worktree data/targets/cloud_segment_04
git update-index --skip-worktree data/targets/cloud_segment_05
git update-index --skip-worktree data/targets/cloud_segment_06
git update-index --skip-worktree data/targets/cloud_segment_07
git update-index --skip-worktree data/targets/cloud_segment_08
git update-index --skip-worktree data/targets/cloud_segment_09
git update-index --skip-worktree data/targets/targets.txt
git update-index --skip-worktree data/targets/targets.txt.bak

echo "✅ 完成。以后 pull 代码不会覆盖以上文件。"
echo "验证: git ls-files -v -- data/targets/ data/leads/ config/settings.yaml | grep '^S'"
