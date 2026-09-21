"""
FollowupWorker — 二次触达（首次问候成功 N 天后）

职责：
  在首次问候成功 N 天后，对已成功添加并完成首次问候的好友，用「原始 device_id」
  打开 LINE，搜索 LINE ID → 三次验证 → 发送二次问候 → 验证成功 → 落库。

硬约束（与首次添加好友任务完全隔离）：
  - 只依赖 adb_operator（UI 操作）+ db.py 的 followup 原语；
  - 绝不调用 account_manager.report() / check_blocked() / record_search() 等，
    避免污染 daily_success / 搜索次数 / 冷却风控；
  - 不调用 scheduler，不改首次添加流程；
  - 同一 device_id 同一时间最多一个 LINE UI 自动化任务（复用进程内设备锁）。

状态机（复用 first_contact_records 表，不改建表）：
  WAITING_FOLLOWUP --claim--> PROCESSING --成功--> FOLLOWUP_SENT
                                        --失败--> attempts+1 --> WAITING_FOLLOWUP(重试) / FOLLOWUP_FAILED
"""
import os
import random
import time
import logging

import yaml

import db

logger = logging.getLogger(__name__)

LINE_PACKAGE = "jp.naver.line.android"

DEFAULT_CONFIG = {
    "enabled": True,
    "base_delay_days": 3,
    "max_attempts": 2,
    "max_per_run": 3,
    "run_hours": [8, 22],
    "messages_file": "/root/line-crm/config/greetings.txt",
    "screenshot_dir": "/app/data/state/followup_shots",
    "followup_schedule": {},
}


class FollowupWorker:
    def __init__(self, adb_op, get_device_lock, config_path=None):
        self.adb_op = adb_op
        self.get_device_lock = get_device_lock
        self.config = self._load_config(config_path)
        self._messages = self._load_messages()

    # ─── 配置 / 消息 ───

    def _load_config(self, path):
        cfg = dict(DEFAULT_CONFIG)
        if not path:
            path = os.environ.get("FOLLOWUP_CONFIG",
                                  "/root/line-crm/config/followup.yaml")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                fcfg = data.get("followup", {}) if isinstance(data, dict) else {}
                for k, v in fcfg.items():
                    if k in DEFAULT_CONFIG and v is not None:
                        cfg[k] = v
            except Exception as e:
                logger.warning("[FOLLOWUP] 配置加载失败 path=%s err=%s", path, e)
        return cfg

    def _load_messages(self):
        path = self.config.get("messages_file", "")
        msgs = []
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            msgs.append(line)
            except Exception as e:
                logger.warning("[FOLLOWUP] 消息文件加载失败 path=%s err=%s", path, e)
        if not msgs:
            msgs = ["你好，最近有資金需求嗎？"]  # 占位，待运营替换
        return msgs

    # ─── 调度入口 ───

    def run_due(self):
        """查询到期任务并逐条执行（非 force：全局窗口 + 时刻表/可用性筛选 + claim + 设备锁）。"""
        if not self.config.get("enabled", True):
            return {"ok": True, "enabled": False, "processed": 0, "results": []}
        if not self._in_run_window():
            logger.info("[FOLLOWUP] SKIPPED_WINDOW")
            return {"ok": True, "enabled": True, "processed": 0, "results": [],
                    "reason": "SKIPPED_WINDOW"}
        max_attempts = int(self.config.get("max_attempts", 2))
        # 崩溃恢复：先回收超时未完成的 PROCESSING，再选候选
        recovered = db.recover_stale_processing(max_attempts=max_attempts)
        if recovered > 0:
            logger.info("[FOLLOWUP] recovered=%d stale PROCESSING task(s)", recovered)
        candidates = self._select_candidates()
        results = []
        processed = 0
        for rec in candidates:
            r = self._process_record(rec, force=False)
            r["id"] = rec.get("id")
            r["device"] = rec.get("device_id", "")
            r["line_id"] = rec.get("line_id", "")
            results.append(r)
            if r["result"] in ("SUCCESS", "FAILED"):
                processed += 1
        return {"ok": True, "enabled": True, "processed": processed,
                "results": results}

    def run_one(self, record, force=True):
        """测试/强制模式：跳过到期判断、时窗与 claim，直接执行单条。"""
        if not record:
            return {"result": "SKIPPED", "reason": "NO_RECORD"}
        return self._process_record(record, force=force)

    # ─── 单条执行（锁内） ───

    def _resolve_addr(self, device_id):
        if device_id and ":" in device_id:
            return device_id
        return self.adb_op.get_device_addr(device_id)

    def _taipei_hour(self):
        """Asia/Taipei（UTC+8，无夏令时）当前小时，显式计算、不依赖进程 TZ 环境变量。"""
        return int((time.time() + 8 * 3600) % 86400) // 3600

    def _in_run_window(self):
        """全局运行窗口：仅在 [run_hours[0], run_hours[1]) 内运行，22:00 后不跑。"""
        hours = self.config.get("run_hours", [8, 22])
        try:
            start, end = int(hours[0]), int(hours[1])
        except Exception:
            start, end = 8, 22
        h = self._taipei_hour()
        return start <= h < end

    def _local_midnight(self, epoch_ts):
        """首次问候 epoch → Taipei（UTC+8）自然日零点，返回该零点的 UTC epoch。"""
        shifted = epoch_ts + 8 * 3600
        taipei_midnight = shifted - (shifted % 86400)
        return taipei_midnight - 8 * 3600

    def _send_start(self, first_greeting_at, sched):
        """每台设备的可发送起始时刻：自然日零点 + day_offset 自然日 + start_hour 整点。"""
        day_offset = int(sched.get("day_offset", self.config.get("base_delay_days", 3)))
        start_hour = int(sched.get("start_hour", 0))
        return self._local_midnight(first_greeting_at) + day_offset * 86400 + start_hour * 3600

    def _device_available(self, st):
        """只读判定设备当前是否可用于 followup（st 为 account_status 行 dict 或 None）。
        与 account_manager.check_blocked 的「硬阻断 + 冷却」语义对齐，但不写、不改状态。"""
        if st is None:
            return True
        status = st.get("status")
        if status in ("banned", "disabled", "dead", "login_error"):
            return False
        if status == "cooldown":
            until = st.get("cooldown_until")
            if until and until > time.time():
                return False
        if status == "search_limit":
            return False  # followup 会搜索，搜索受限同样跳过
        return True

    def _select_candidates(self):
        """从全部 WAITING_FOLLOWUP 中筛出本轮可执行候选（最多 max_per_run 条）：
        设备在 followup_schedule 内 + account_status 可用 + 已到该设备顺延后的 send_start。
        跳过不可用设备时继续检查后续，不阻塞整个队列。返回按 send_start 升序的候选列表。"""
        schedule = self.config.get("followup_schedule", {})
        max_per_run = int(self.config.get("max_per_run", 3))
        now = time.time()
        records = db.list_waiting_followup()
        status_cache = {}
        logged_unavailable = set()
        cands = []
        for rec in records:
            dev = rec.get("device_id", "")
            if dev not in schedule:
                continue  # 待换号 / 不在 followup 计划内，不参与
            if dev not in status_cache:
                status_cache[dev] = db.get_device_status(dev)
            st = status_cache[dev]
            if not self._device_available(st):
                if dev not in logged_unavailable:
                    logger.info("[FOLLOWUP] device=%s result=SKIPPED reason=SKIPPED_DEVICE_UNAVAILABLE status=%s",
                                dev, (st or {}).get("status"))
                    logged_unavailable.add(dev)
                continue
            send_start = self._send_start(rec["first_greeting_at"], schedule[dev])
            if now < send_start:
                continue  # 未到该设备顺延后的可发送时刻
            cands.append((send_start, rec["first_greeting_at"], rec))
        cands.sort(key=lambda x: (x[0], x[1]))
        return [rec for _, _, rec in cands[:max_per_run]]

    def _process_record(self, record, force=False):
        device_id = record.get("device_id", "")
        line_id = record.get("line_id", "")
        task_id = record.get("id")
        addr = self._resolve_addr(device_id)

        lock = self.get_device_lock(addr)
        if not lock.acquire(blocking=False):
            # 设备正被加好友/auto-reply 占用：不 claim、不消耗 attempt，任务保持 WAITING_FOLLOWUP
            logger.info("[FOLLOWUP] id=%s device=%s line_id=%s result=SKIPPED reason=SKIPPED_DEVICE_BUSY",
                        task_id, device_id, line_id)
            return {"result": "SKIPPED", "reason": "SKIPPED_DEVICE_BUSY"}

        try:
            # 领取：原子 claim，保证「同一任务同一时间只被一个 worker 执行」
            if not force and not db.claim_followup_task(task_id):
                return {"result": "SKIPPED", "reason": "ALREADY_CLAIMED"}

            if not self.adb_op.ensure_connected(addr):
                return self._fail(task_id, device_id, addr, line_id, "ADB_DISCONNECTED")

            ok, reason = self._do_flow(device_id, addr, line_id)
            if ok:
                db.mark_followup_sent(task_id)
                logger.info("[FOLLOWUP] id=%s device=%s line_id=%s result=SUCCESS",
                            task_id, device_id, line_id)
                return {"result": "SUCCESS", "reason": reason}
            return self._fail(task_id, device_id, addr, line_id, reason)
        finally:
            lock.release()

    def _fail(self, task_id, device_id, addr, line_id, reason):
        shot = self._save_screenshot(addr, device_id, reason)
        final_status = db.mark_followup_failed(
            task_id, reason, int(self.config.get("max_attempts", 2)))
        logger.info("[FOLLOWUP] id=%s device=%s line_id=%s result=FAILED reason=%s final=%s shot=%s",
                    task_id, device_id, line_id, reason, final_status, shot)
        return {"result": "FAILED", "reason": reason,
                "final_status": final_status, "screenshot": shot}

    # ─── UI 流程 ───

    def _do_flow(self, device_id, addr, line_id):
        """严格按 spec：主页→搜索→三次验证→发送→验证。返回 (ok, reason)。"""
        # 1. 打开 LINE → 主页
        self.adb_op.open_line(addr)
        self._ensure_home(addr)

        # 2. 主页搜索框 → 输入 LINE ID → 搜索
        if not self._home_search(addr, line_id):
            return False, "SEARCH_BOX_NOT_FOUND"

        # 3. 搜索结果验证：结果含目标 LINE ID 才点好友
        ok, reason = self._search_result_verify_and_tap(addr, line_id)
        if not ok:
            return False, reason

        # 4. 资料页验证：显示名 == LINE ID
        if not self._profile_verify(addr, line_id):
            return False, "PROFILE_NOT_MATCH"

        # 5. 点「聊天」+ 聊天页验证：聊天对象 == LINE ID
        ok, reason = self._open_chat_and_verify(addr, line_id)
        if not ok:
            return False, reason

        # 6. 发送二次问候 + 验证输入框清空
        message = random.choice(self._messages)
        ok, reason = self._send_message(addr, message)
        if not ok:
            return False, reason

        return True, "SUCCESS"

    def _ensure_home(self, addr):
        d = self.adb_op.get_u2(addr)
        home_el = d(text="主页")
        if home_el.exists(timeout=2):
            try:
                home_el.click()
                time.sleep(2)
                return True
            except Exception:
                pass
        self.adb_op.adb(addr, "shell", "input", "tap", "72", "1128")
        time.sleep(2)
        return True

    def _home_search(self, addr, line_id):
        """主页顶部搜索入口 → 输入 LINE ID → 回车搜索。"""
        d = self.adb_op.get_u2(addr)
        entry = d(text="搜索")
        if not entry.exists(timeout=1):
            entry = d(description="搜索")
        if not entry.exists(timeout=1):
            entry = d(descriptionContains="搜索")
        if entry.exists(timeout=1):
            try:
                entry.click()
            except Exception:
                self.adb_op.adb(addr, "shell", "input", "tap", "650", "80")
        else:
            # 兜底：send-message 已验坐标
            self.adb_op.adb(addr, "shell", "input", "tap", "650", "80")
        time.sleep(2)
        self.adb_op.type_text(addr, line_id)
        time.sleep(0.5)
        self.adb_op.adb(addr, "shell", "input", "keyevent", "66")  # 回车搜索
        time.sleep(3)
        return True

    def _search_result_verify_and_tap(self, addr, line_id):
        self.adb_op.clear_u2_cache(addr)
        d = self.adb_op.get_u2(addr)
        xml = d.dump_hierarchy()
        if "未找到" in xml or "查無" in xml:
            return False, "SEARCH_NOT_FOUND"
        for kw in ["已达上限", "搜索次数", "过于频繁", "稍后再试", "限制"]:
            if kw in xml:
                return False, "SEARCH_LIMIT"
        # 找结果行：text==line_id 且非 EditText 且位于结果区（排除顶部输入框）
        target = None
        for el in d(text=line_id):
            try:
                info = el.info
                if info.get("className", "") == "android.widget.EditText":
                    continue
                b = info.get("bounds", {})
                cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
                if 150 < cy < 1000:
                    target = el
                    break
            except Exception:
                continue
        if target is None:
            return False, "SEARCH_RESULT_NOT_MATCH"
        try:
            target.click()
        except Exception:
            return False, "SEARCH_RESULT_NOT_MATCH"
        time.sleep(2)
        return True, "OK"

    def _profile_verify(self, addr, line_id):
        self.adb_op.clear_u2_cache(addr)
        d = self.adb_op.get_u2(addr)
        name = ""
        name_el = d(resourceId="jp.naver.line.android:id/user_profile_name")
        if name_el.exists(timeout=2):
            name = (name_el.get_text() or "").strip()
        if not name:
            # 兜底：结果区文本 == line_id
            for el in d(text=line_id):
                try:
                    b = el.info.get("bounds", {})
                    cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
                    if 100 < cy < 400:
                        name = line_id
                        break
                except Exception:
                    continue
        return name == line_id

    def _open_chat_and_verify(self, addr, line_id):
        d = self.adb_op.get_u2(addr)
        chat_btn = d(text="聊天")
        if not chat_btn.exists(timeout=1):
            chat_btn = d(description="聊天")
        if not chat_btn.exists(timeout=1):
            chat_btn = d(descriptionContains="聊天")
        if chat_btn.exists(timeout=2):
            try:
                chat_btn.click()
                time.sleep(2)
            except Exception:
                return False, "CHAT_BUTTON_NOT_FOUND"
        else:
            # 可能已直接进入聊天页（无资料页按钮），继续验证
            inp = d(description="输入消息")
            if not inp.exists(timeout=2):
                return False, "CHAT_BUTTON_NOT_FOUND"

        # 聊天页验证：顶部标题 == LINE ID
        self.adb_op.clear_u2_cache(addr)
        d2 = self.adb_op.get_u2(addr)
        title = ""
        title_el = d2(resourceId="jp.naver.line.android:id/chat_ui_title")
        if title_el.exists(timeout=1):
            title = (title_el.get_text() or "").strip()
        if not title:
            for el in d2(text=line_id):
                try:
                    b = el.info.get("bounds", {})
                    cy = (int(b.get("top", 0)) + int(b.get("bottom", 0))) // 2
                    if cy < 150:
                        title = line_id
                        break
                except Exception:
                    continue
        if title != line_id:
            return False, "CHAT_TARGET_NOT_MATCH"
        return True, "OK"

    def _send_message(self, addr, message):
        d = self.adb_op.get_u2(addr)
        inp = d(description="输入消息")
        if not inp.exists(timeout=2):
            inp = d(resourceId="jp.naver.line.android:id/chat_ui_message_edit")
        if not inp.exists(timeout=2):
            return False, "CHAT_INPUT_NOT_FOUND"
        inp.click()
        time.sleep(0.3)
        self.adb_op.type_text(addr, message)
        time.sleep(0.5)
        send_btn = d(description="发送")
        if send_btn.exists(timeout=3):
            send_btn.click()
        else:
            self.adb_op.adb(addr, "shell", "input", "keyevent", "66")
        time.sleep(1)

        # 验证发送成功：输入框清空
        self.adb_op.clear_u2_cache(addr)
        dchk = self.adb_op.get_u2(addr)
        edit_el = dchk(resourceId="jp.naver.line.android:id/chat_ui_message_edit")
        if not edit_el.exists(timeout=2):
            edit_el = dchk(description="输入消息")
        if not edit_el.exists(timeout=1):
            return True, "SUCCESS"  # 读不到输入框，退回旧逻辑
        remaining = ""
        try:
            remaining = (edit_el.get_text() or "").strip()
        except Exception:
            remaining = ""
        if remaining == "":
            return True, "SUCCESS"
        return False, "SEND_FAILED"

    # ─── 截图留证 ───

    def _save_screenshot(self, addr, device_id, reason):
        try:
            r = self.adb_op.adb_raw(addr, "exec-out", "screencap", "-p")
            if not r["ok"]:
                return ""
            d = self.config.get("screenshot_dir", "")
            if not d:
                return ""
            os.makedirs(d, exist_ok=True)
            fname = "%s_%d_%s.png" % (device_id, int(time.time()), reason)
            path = os.path.join(d, fname)
            with open(path, "wb") as f:
                f.write(r["stdout_bytes"])
            return path
        except Exception as e:
            logger.warning("[FOLLOWUP] 截图保存失败 err=%s", e)
            return ""
