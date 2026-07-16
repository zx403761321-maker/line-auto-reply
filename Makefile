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
	@echo "=== 生产桥 ===" && curl -s --max-time 5 http://127.0.0.1:8899/health | python3 -m json.tool 2>/dev/null || echo "❌"
	@echo "=== 测试桥 ===" && curl -s --max-time 5 http://127.0.0.1:8898/health | python3 -m json.tool 2>/dev/null || echo "⏸️  未运行"
	@echo "=== 云手机 ==="
	@for ip in 39.109.41.52:499 39.109.41.74:499 39.109.41.244:498 39.109.43.51:499 39.109.37.123:500 39.109.42.124:500 39.109.42.197:500 39.109.42.173:500 39.109.42.47:500 39.109.42.126:500; do \
		r=$$(adb -s $$ip shell echo ok 2>&1); \
		[ "$$r" = "ok" ] && echo "  ✅ $$ip" || echo "  ❌ $$ip"; \
	done

clean:
	@rm -f data/state/targets_position_shared
	@echo "进度已清零"

report:
	@bash scripts/report.sh
