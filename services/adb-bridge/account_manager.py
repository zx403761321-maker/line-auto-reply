"""
Account Manager — LINE 账号状态管理模块

职责：
  1. 查询账号状态
  2. 更新账号状态
  3. 更新统计信息
  4. 判断账号今天是否还能执行任务
  5. 判断账号是否需要进入冷却

不负责 ADB，不负责 LINE 操作，纯状态管理。
线程模型与 db.py 一致：threading.local + WAL，静默失败。
"""
import sqlite3
import time
import os
import threading
import json

DB_PATH = os.environ.get("DB_PATH", "/app/data/bridge.db")
_conn_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取线程本地数据库连接（WAL 模式）"""
    if not hasattr(_conn_local, "conn") or _conn_local.conn is None:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.row_factory = sqlite3.Row
        _conn_local.conn = conn
    return _conn_local.conn


class AccountManager:
    """LINE 账号状态管理器 — 每台设备对应一个 LINE 账号"""

    def __init__(self, db_path: str = None):
        if db_path:
            global DB_PATH
            DB_PATH = db_path
        self._init_table()

    # ─── 建表 ───

    def _init_table(self):
        """初始化 account_status 表（幂等）"""
        try:
            conn = _get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account_status (
                    device_id        TEXT PRIMARY KEY,
                    addr             TEXT NOT NULL,
                    label            TEXT,
                    status           TEXT NOT NULL DEFAULT 'active',
                    last_task_at     REAL,
                    last_task_type   TEXT,
                    last_error       TEXT,
                    last_error_at    REAL,

                    -- 每日统计
                    daily_success    INTEGER DEFAULT 0,
                    daily_fail       INTEGER DEFAULT 0,
                    daily_search     INTEGER DEFAULT 0,
                    daily_reset_at   TEXT DEFAULT '00:00',

                    -- 限额
                    daily_add_limit     INTEGER DEFAULT 10,
                    daily_search_limit  INTEGER DEFAULT 50,

                    -- 冷却机制
                    cooldown_until         REAL,
                    cooldown_reason        TEXT,
                    consecutive_fails      INTEGER DEFAULT 0,
                    max_consecutive_fails  INTEGER DEFAULT 10,
                    cooldown_minutes       INTEGER DEFAULT 30,

                    -- 累计统计
                    total_add_attempts  INTEGER DEFAULT 0,
                    total_add_success   INTEGER DEFAULT 0,
                    total_reply_count   INTEGER DEFAULT 0,

                    -- 扩展
                    extra_json      TEXT DEFAULT '{}',
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL
                );
            """)
            conn.commit()
        except Exception:
            pass

    # ─── 查询 ───

    def get(self, device_id: str) -> dict | None:
        """查询单个账号完整状态"""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT * FROM account_status WHERE device_id = ?",
                (device_id,)).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def list_accounts(self, status: str = None) -> list[dict]:
        """列出所有账号，可按 status 过滤"""
        try:
            conn = _get_conn()
            if status:
                rows = conn.execute(
                    "SELECT * FROM account_status WHERE status = ?",
                    (status,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM account_status ORDER BY device_id"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ─── 初始化 / 更新基本信息 ───

    def ensure_account(self, device_id: str, addr: str, label: str = "",
                       **kwargs) -> bool:
        """
        幂等：不存在则 INSERT，存在则 UPDATE 基本信息（addr, label + kwargs）。
        kwargs 可覆盖任意列：daily_add_limit, max_consecutive_fails, cooldown_minutes ...
        """
        try:
            conn = _get_conn()
            now = time.time()
            existing = conn.execute(
                "SELECT 1 FROM account_status WHERE device_id = ?",
                (device_id,)).fetchone()

            if not existing:
                defaults = {
                    "addr": addr, "label": label,
                    "daily_add_limit": 10, "daily_search_limit": 50,
                    "max_consecutive_fails": 10, "cooldown_minutes": 30,
                    "status": "active",
                }
                defaults.update(kwargs)
                conn.execute("""
                    INSERT INTO account_status
                        (device_id, addr, label, status,
                         daily_add_limit, daily_search_limit,
                         max_consecutive_fails, cooldown_minutes,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (device_id, defaults["addr"], defaults["label"],
                      defaults["status"],
                      defaults["daily_add_limit"], defaults["daily_search_limit"],
                      defaults["max_consecutive_fails"], defaults["cooldown_minutes"],
                      now, now))
            else:
                updates = {}
                updates["addr"] = addr
                if label:
                    updates["label"] = label
                updates.update(kwargs)
                sets = ", ".join(f"{k}=?" for k in updates)
                vals = list(updates.values()) + [now, device_id]
                conn.execute(
                    f"UPDATE account_status SET {sets}, updated_at=? WHERE device_id=?",
                    vals)
            conn.commit()
            return True
        except Exception:
            return False

    # ─── 状态更新 ───

    def update_status(self, device_id: str, status: str = None,
                      **extra) -> bool:
        """
        更新账号状态。extra 可传任意字段：daily_add_limit, cooldown_until, ...
        自动检查 daily_reset（跨天归零）。
        """
        try:
            self._maybe_reset_daily(device_id)
            conn = _get_conn()
            now = time.time()
            parts = []
            vals = []
            if status is not None:
                parts.append("status=?")
                vals.append(status)
            for k, v in extra.items():
                parts.append(f"{k}=?")
                vals.append(v)
            parts.append("updated_at=?")
            vals.append(now)
            vals.append(device_id)
            conn.execute(
                f"UPDATE account_status SET {', '.join(parts)} WHERE device_id=?",
                vals)
            conn.commit()
            return True
        except Exception:
            return False

    # ─── 记录成功 / 失败 ───

    def record_success(self, device_id: str,
                       task_type: str = "add_friend") -> bool:
        """记录一次任务成功：daily_success+1，累计+1，连续失败清零"""
        try:
            self._maybe_reset_daily(device_id)
            conn = _get_conn()
            now = time.time()
            conn.execute("""
                UPDATE account_status
                SET daily_success = daily_success + 1,
                    total_add_attempts = total_add_attempts + 1,
                    total_add_success = total_add_success + 1,
                    consecutive_fails = 0,
                    last_task_at = ?, last_task_type = ?,
                    last_error = NULL, last_error_at = NULL,
                    cooldown_until = NULL,
                    updated_at = ?
                WHERE device_id = ?
            """, (now, task_type, now, device_id))
            conn.commit()
            return True
        except Exception:
            return False

    def record_failure(self, device_id: str, error: str = "",
                       task_type: str = "add_friend") -> bool:
        """
        记录一次任务失败：daily_fail+1，consecutive_fail+1。
        超过 max_consecutive_fails 自动 enter_cooldown。
        """
        try:
            self._maybe_reset_daily(device_id)
            conn = _get_conn()
            now = time.time()
            conn.execute("""
                UPDATE account_status
                SET daily_fail = daily_fail + 1,
                    total_add_attempts = total_add_attempts + 1,
                    consecutive_fails = consecutive_fails + 1,
                    last_task_at = ?, last_task_type = ?,
                    last_error = ?, last_error_at = ?,
                    updated_at = ?
                WHERE device_id = ?
            """, (now, task_type, str(error)[:500], now, now, device_id))
            conn.commit()

            # 检查是否触发冷却
            row = conn.execute(
                "SELECT consecutive_fails, max_consecutive_fails "
                "FROM account_status WHERE device_id = ?",
                (device_id,)).fetchone()
            if row and row["consecutive_fails"] >= row["max_consecutive_fails"]:
                self.enter_cooldown(device_id,
                                    f"连续失败{row['consecutive_fails']}次")
            return True
        except Exception:
            return False

    def record_search(self, device_id: str, count: int = 1) -> bool:
        """记录搜索次数"""
        try:
            self._maybe_reset_daily(device_id)
            conn = _get_conn()
            now = time.time()
            conn.execute(
                "UPDATE account_status SET daily_search = daily_search + ?, "
                "updated_at = ? WHERE device_id = ?",
                (count, now, device_id))
            conn.commit()
            return True
        except Exception:
            return False

    # ─── 可执行判断 ───

    def can_execute(self, device_id: str,
                    task_type: str = "add_friend") -> tuple[bool, str]:
        """
        判断账号能否执行任务。
        返回 (bool, reason):
          - (True, "ok")           可执行
          - (False, "banned")      账号被封
          - (False, "disabled")    已禁用
          - (False, "cooldown")    冷却中
          - (False, "daily_limit") 今日已达上限
          - (False, "not_found")   账号不存在
        """
        try:
            self._maybe_reset_daily(device_id)
            conn = _get_conn()
            row = conn.execute(
                "SELECT status, daily_success, daily_add_limit, "
                "cooldown_until FROM account_status WHERE device_id = ?",
                (device_id,)).fetchone()

            if not row:
                return (False, "not_found")

            if row["status"] == "banned":
                return (False, "banned")
            if row["status"] == "disabled":
                return (False, "disabled")
            if row["status"] == "cooldown":
                if row["cooldown_until"] and row["cooldown_until"] > time.time():
                    return (False, "cooldown")

            if task_type == "add_friend":
                if row["daily_success"] >= row["daily_add_limit"]:
                    return (False, "daily_limit")

            return (True, "ok")
        except Exception:
            return (True, "ok")  # DB 故障时放行，不阻塞业务

    def can_search(self, device_id: str) -> tuple[bool, str]:
        """判断能否继续搜索（检查每日搜索上限）"""
        try:
            self._maybe_reset_daily(device_id)
            conn = _get_conn()
            row = conn.execute(
                "SELECT daily_search, daily_search_limit "
                "FROM account_status WHERE device_id = ?",
                (device_id,)).fetchone()
            if not row:
                return (True, "ok")
            if row["daily_search"] >= row["daily_search_limit"]:
                return (False, "search_limit")
            return (True, "ok")
        except Exception:
            return (True, "ok")

    # ─── 冷却 ───

    def enter_cooldown(self, device_id: str, reason: str = "",
                       minutes: int = None) -> bool:
        """进入冷却状态"""
        try:
            conn = _get_conn()
            now = time.time()
            row = conn.execute(
                "SELECT cooldown_minutes FROM account_status WHERE device_id = ?",
                (device_id,)).fetchone()
            if not row:
                return False
            mins = minutes if minutes is not None else row["cooldown_minutes"]
            cooldown_until = now + mins * 60
            conn.execute("""
                UPDATE account_status
                SET status = 'cooldown',
                    cooldown_until = ?, cooldown_reason = ?,
                    updated_at = ?
                WHERE device_id = ?
            """, (cooldown_until, str(reason)[:200], now, device_id))
            conn.commit()
            return True
        except Exception:
            return False

    def exit_cooldown(self, device_id: str) -> bool:
        """退出冷却，恢复 active 并清零连续失败"""
        try:
            conn = _get_conn()
            now = time.time()
            conn.execute("""
                UPDATE account_status
                SET status = 'active',
                    cooldown_until = NULL, cooldown_reason = NULL,
                    consecutive_fails = 0,
                    updated_at = ?
                WHERE device_id = ?
            """, (now, device_id))
            conn.commit()
            return True
        except Exception:
            return False

    # ─── 每日重置 ───

    def _maybe_reset_daily(self, device_id: str):
        """如果跨天了，自动重置每日计数器"""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT daily_reset_at, updated_at FROM account_status "
                "WHERE device_id = ?", (device_id,)).fetchone()
            if not row:
                return
            reset_at = row["daily_reset_at"] or "00:00"
            last_updated = row["updated_at"]
            if not last_updated:
                return

            # 解析 reset_at 为今天的小时和分钟
            parts = reset_at.split(":")
            reset_hour = int(parts[0]) if parts else 0
            reset_min = int(parts[1]) if len(parts) > 1 else 0

            now = time.localtime(time.time())
            last = time.localtime(last_updated)

            # 判断是否跨了重置点（用日期比较：last < today_reset 且 now >= today_reset）
            today_reset_ts = time.mktime((
                now.tm_year, now.tm_mon, now.tm_mday,
                reset_hour, reset_min, 0,
                now.tm_wday, now.tm_yday, now.tm_isdst))

            if last_updated < today_reset_ts <= time.time():
                conn.execute("""
                    UPDATE account_status
                    SET daily_success = 0, daily_fail = 0, daily_search = 0,
                        updated_at = ?
                    WHERE device_id = ?
                """, (time.time(), device_id))
                conn.commit()
        except Exception:
            pass

    def reset_daily(self, device_id: str) -> bool:
        """手动重置每日计数"""
        try:
            conn = _get_conn()
            conn.execute("""
                UPDATE account_status
                SET daily_success = 0, daily_fail = 0, daily_search = 0,
                    updated_at = ?
                WHERE device_id = ?
            """, (time.time(), device_id))
            conn.commit()
            return True
        except Exception:
            return False

    # ─── 统计 ───

    def get_stats(self, device_id: str) -> dict:
        """返回单个账号的统计摘要"""
        try:
            row = self.get(device_id)
            if not row:
                return {}
            return {
                "device_id": row["device_id"],
                "addr": row["addr"],
                "label": row["label"],
                "status": row["status"],
                "daily": {
                    "success": row["daily_success"],
                    "fail": row["daily_fail"],
                    "search": row["daily_search"],
                    "add_limit": row["daily_add_limit"],
                    "search_limit": row["daily_search_limit"],
                },
                "cooldown": {
                    "until": row["cooldown_until"],
                    "reason": row["cooldown_reason"],
                    "consecutive_fails": row["consecutive_fails"],
                },
                "total": {
                    "add_attempts": row["total_add_attempts"],
                    "add_success": row["total_add_success"],
                    "reply_count": row["total_reply_count"],
                },
                "last_task": {
                    "at": row["last_task_at"],
                    "type": row["last_task_type"],
                    "error": row["last_error"],
                },
            }
        except Exception:
            return {}

    def get_daily_summary(self) -> list[dict]:
        """所有活跃账号的今日统计摘要（用于日报）"""
        try:
            accounts = self.list_accounts()
            result = []
            for a in accounts:
                result.append({
                    "device_id": a["device_id"],
                    "label": a["label"],
                    "status": a["status"],
                    "daily_success": a["daily_success"],
                    "daily_fail": a["daily_fail"],
                    "daily_search": a["daily_search"],
                    "add_limit": a["daily_add_limit"],
                    "consecutive_fails": a["consecutive_fails"],
                    "cooldown_until": a["cooldown_until"],
                })
            return result
        except Exception:
            return []
