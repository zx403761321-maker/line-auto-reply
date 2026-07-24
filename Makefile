.PHONY: start stop status logs deploy health clean

DEVICES ?= cloud-01 cloud-06 cloud-07 cloud-08 cloud-09
TEST_DEVICES ?=

start:
	@for dev in $(DEVICES); do \
		nohup bash scripts/template.sh $$dev > /dev/null 2>&1 & \
		echo "✅ $$dev (PID: $$!)"; \
	done
	@for dev in $(TEST_DEVICES); do \
		BRIDGE=http://127.0.0.1:8898 nohup bash scripts/template.sh $$dev > /dev/null 2>&1 & \
		echo "✅ $$dev 测试桥 (PID: $$!)"; \
	done

stop:
	@pkill -f "template.sh" 2>/dev/null && echo "已停止所有" || echo "无运行进程"

status:
	@ps aux | grep "template.sh" | grep -v grep | awk '{print "🟢", $$11, $$12}' || echo "无运行进程"
	@echo "---"
	@curl -s --max-time 3 http://127.0.0.1:8899/health 2>/dev/null | python3 -m json.tool || echo "🔴 生产桥挂了"
	@echo "---"
	@echo "进度: $$(cat data/state/targets_position_shared 2>/dev/null || echo '?')"

logs:
	@tail -f logs/daily_add.log

deploy:
	@bash scripts/template.sh --check 2>/dev/null || { echo "❌ 模板语法错误"; exit 1; }
	@for n in 01 02 03 04 05 06 07 08 09 10; do \
		sed "s/\$$1/cloud-$$n/" scripts/template.sh > scripts/daily_add_cloud-$$n.sh; \
		chmod +x scripts/daily_add_cloud-$$n.sh; \
	done
	@git add scripts/ && git diff --cached --stat && echo "make commit 提交"

health:
	@echo "=== Bridge ===" && curl -s --max-time 5 http://127.0.0.1:8899/health | python3 -m json.tool 2>/dev/null || echo "❌"
	@echo "=== 账号状态 (DB) ===" && docker exec openclaw-adb-bridge python3 -c "import sqlite3;conn=sqlite3.connect('/app/data/bridge.db');conn.row_factory=sqlite3.Row;rows=conn.execute('SELECT device_id,status,daily_success,daily_search,risk_score FROM account_status ORDER BY device_id').fetchall();[print(f\"  {'🟢' if r['status']=='online' else '🔴'} {r['device_id']:12s} success={r['daily_success']:2d} search={r['daily_search']:2d} risk={r['risk_score']}\") for r in rows]"

clean:
	@rm -f data/state/targets_position_shared
	@echo "进度已清零"

report:
push:
	@bash scripts/push_report.sh
	@bash scripts/report.sh

build:
	@docker compose build adb-bridge
	@echo "✅ Bridge 镜像构建完成"
