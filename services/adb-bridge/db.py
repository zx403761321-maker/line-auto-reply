"""
SQLite 持久化模块 — 设备状态 + 任务记录 + AI 回复日志
每条记录自动过期清理（默认保留 30 天）
"""
import sqlite3
import time
import os
import threading
import logging

DB_PATH = os.environ.get("DB_PATH", "/app/data/bridge.db")

_conn_local = threading.local()

logger = logging.getLogger(__name__)


def _get_conn() -> sqlite3.Connection:
    """获取线程本地数据库连接（WAL 模式，支持并发读写）"""
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


def init_db():
    """初始化数据库表（幂等）"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS device_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            addr TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            recorded_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ds_device
            ON device_status(device_id, recorded_at DESC);

        CREATE TABLE IF NOT EXISTS task_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            addr TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            line_id TEXT,
            steps INTEGER DEFAULT 0,
            last_step TEXT,
            replied_count INTEGER DEFAULT 0,
            duration_ms REAL DEFAULT 0,
            error TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tl_device
            ON task_log(device_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tl_type
            ON task_log(task_type, created_at DESC);

        CREATE TABLE IF NOT EXISTS reply_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            contact TEXT,
            msg_preview TEXT,
            reply_preview TEXT,
            intent_level TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rl_device
            ON reply_log(device_id, created_at DESC);
    """)
    conn.commit()
    _cleanup_old(conn, days=30)


def _cleanup_old(conn, days=30):
    """清理过期记录（每次 init 时执行一次）"""
    cutoff = time.time() - days * 86400
    try:
        conn.execute("DELETE FROM device_status WHERE recorded_at < ?", (cutoff,))
        conn.execute("DELETE FROM task_log WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM reply_log WHERE created_at < ?", (cutoff,))
        conn.commit()
    except Exception:
        pass


# ─── 写入接口（静默失败，不影响主流程） ───

def record_device_status(device_id: str, addr: str, status: str,
                         detail: str = ""):
    """记录设备上下线状态"""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO device_status (device_id, addr, status, detail, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (device_id, addr, status, str(detail)[:500], time.time()))
        conn.commit()
    except Exception:
        pass


def record_task(device_id: str, addr: str, task_type: str,
                status: str = "ok", line_id: str = "",
                steps: int = 0, last_step: str = "",
                replied_count: int = 0, duration_ms: float = 0,
                error: str = ""):
    """记录任务执行（加好友/自动回复/发消息）"""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO task_log"
            " (device_id, addr, task_type, status, line_id, steps, last_step,"
            "  replied_count, duration_ms, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, addr, task_type, status,
             str(line_id)[:100], steps, str(last_step)[:50],
             replied_count, duration_ms, str(error)[:200], time.time()))
        conn.commit()
    except Exception:
        pass


def record_reply(device_id: str, contact: str, msg_preview: str,
                 reply_preview: str, intent_level: str = ""):
    """记录 AI 回复内容（用于后续分析转化率）"""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO reply_log"
            " (device_id, contact, msg_preview, reply_preview, intent_level, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, str(contact)[:100], str(msg_preview)[:100],
             str(reply_preview)[:100], intent_level, time.time()))
        conn.commit()
    except Exception:
        pass


# ─── 首次触达记录（首次添加好友 + 首次问候成功） ───

_first_contact_ready = False
_first_contact_lock = threading.Lock()


def _ensure_first_contact_table():
    """幂等创建 first_contact_records 表（独立于 init_db，避免依赖启动调用顺序）"""
    global _first_contact_ready
    if _first_contact_ready:
        return
    with _first_contact_lock:
        if _first_contact_ready:
            return
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS first_contact_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                first_greeting_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'WAITING_FOLLOWUP',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                followup_attempts INTEGER NOT NULL DEFAULT 0,
                followup_sent_at REAL,
                last_error TEXT,
                UNIQUE(device_id, line_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fcr_status
                ON first_contact_records(status, first_greeting_at DESC);
        """)
        # 兼容上一阶段已存在的旧表：幂等补列（重复列报错则忽略）
        for col_ddl in (
            "ALTER TABLE first_contact_records ADD COLUMN followup_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE first_contact_records ADD COLUMN followup_sent_at REAL",
            "ALTER TABLE first_contact_records ADD COLUMN last_error TEXT",
        ):
            try:
                conn.execute(col_ddl)
            except Exception:
                pass
        conn.commit()
        _first_contact_ready = True


def record_first_greeting_success(device_id: str, line_id: str,
                                  status: str = "WAITING_FOLLOWUP") -> bool:
    """记录「首次问候成功」：device_id + line_id 唯一，幂等 upsert（重复调用不产生重复记录）。
    返回是否写入成功，供上层记录 [CONTACT] 日志与失败状态。"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO first_contact_records"
            " (device_id, line_id, first_greeting_at, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(device_id, line_id) DO NOTHING",
            (str(device_id), str(line_id)[:100], now, status, now, now))
        conn.commit()
        return True
    except Exception:
        return False


def get_first_contact(device_id: str, line_id: str):
    """查询某设备+LINE ID 的首次触达记录（供测试/验证用）"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM first_contact_records"
            " WHERE device_id = ? AND line_id = ?",
            (str(device_id), str(line_id)[:100])).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def count_first_contacts():
    """统计首次触达记录总数（供测试/验证用）"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        return conn.execute("SELECT COUNT(*) FROM first_contact_records").fetchone()[0]
    except Exception:
        return -1


# ─── 二次触达（followup）原语 ───
# 只读写 first_contact_records 自身的 followup 列，不碰 task_log / reply_log / device_status，
# 也不调用 account_manager，保证与首次添加好友任务完全隔离。

def list_waiting_followup():
    """返回全部 status='WAITING_FOLLOWUP' 记录（不做 72h 到期过滤），按 first_greeting_at 升序。
    是否「到期」由 followup.py 依据「自然日 + 组别顺延」的 send_start 逐台判断，故此处只取候选全集。"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM first_contact_records"
            " WHERE status = 'WAITING_FOLLOWUP'"
            " ORDER BY first_greeting_at ASC").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_device_status(device_id: str):
    """只读查询 account_status 单行（供 followup 做可用性判断）。只读、不写、不调 account_manager。
    表由 account_manager 单例在进程启动时建好；此处任何异常一律返回 None（视为「无限制/可用」）。"""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM account_status WHERE device_id = ?",
            (device_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def claim_followup_task(task_id: int) -> bool:
    """原子领取：仅当 status='WAITING_FOLLOWUP' 时才置为 PROCESSING。
    用 rowcount 判定，保证「同一任务同一时间只被一个 worker 执行」。"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE first_contact_records SET status = 'PROCESSING', updated_at = ?"
            " WHERE id = ? AND status = 'WAITING_FOLLOWUP'",
            (time.time(), int(task_id)))
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        return False


def recover_stale_processing(timeout_seconds: int = 600, max_attempts: int = 2) -> int:
    """崩溃恢复：把「领取后超时未完成」的 PROCESSING 任务回收。

    - 只处理 status='PROCESSING' 且 updated_at <= now - timeout_seconds 的记录；
    - 一次 claim/执行算一次 attempt：恢复时 followup_attempts + 1；
    - 达到 max_attempts 则转 FOLLOWUP_FAILED（last_error=CRASH_RECOVERED，避免崩溃无限重试），
      否则回 WAITING_FOLLOWUP；两种路径都更新 updated_at。
    - 返回本次恢复的条数（失败返回 -1）。
    """
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        now = time.time()
        cutoff = now - int(timeout_seconds)
        rows = conn.execute(
            "SELECT id, device_id, line_id, followup_attempts, updated_at"
            " FROM first_contact_records"
            " WHERE status = 'PROCESSING' AND updated_at <= ?",
            (cutoff,)).fetchall()
        recovered = 0
        for r in rows:
            new_attempts = (r["followup_attempts"] or 0) + 1
            duration = now - (r["updated_at"] or now)
            final_status = "FOLLOWUP_FAILED" if new_attempts >= int(max_attempts) else "WAITING_FOLLOWUP"
            cur = conn.execute(
                "UPDATE first_contact_records SET status = ?, followup_attempts = ?,"
                " last_error = ?, updated_at = ?"
                " WHERE id = ? AND status = 'PROCESSING' AND updated_at <= ?",
                (final_status, new_attempts, "CRASH_RECOVERED", now, r["id"], cutoff))
            if cur.rowcount == 1:
                recovered += 1
                logger.info("[FOLLOWUP] recover id=%s device=%s line_id=%s stale=%.0fs attempts=%d -> %s",
                            r["id"], r["device_id"], r["line_id"], duration, new_attempts, final_status)
        conn.commit()
        return recovered
    except Exception as e:
        logger.warning("[FOLLOWUP] recover_stale_processing failed: %s", e)
        return -1


def mark_followup_sent(task_id: int) -> bool:
    """标记二次触达成功：status=FOLLOWUP_SENT，attempts+1，followup_sent_at=now，last_error 清空。"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE first_contact_records SET"
            " status = 'FOLLOWUP_SENT',"
            " followup_attempts = followup_attempts + 1,"
            " followup_sent_at = ?,"
            " last_error = NULL,"
            " updated_at = ?"
            " WHERE id = ?",
            (time.time(), time.time(), int(task_id)))
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        return False


def mark_followup_failed(task_id: int, error: str, max_attempts: int) -> str:
    """标记二次触达失败：attempts+1；达到 max_attempts 转 FOLLOWUP_FAILED，否则回 WAITING_FOLLOWUP 待重试。
    返回最终状态（FOLLOWUP_FAILED 或 WAITING_FOLLOWUP），供上层记录日志。"""
    try:
        _ensure_first_contact_table()
        conn = _get_conn()
        now = time.time()
        row = conn.execute(
            "SELECT followup_attempts FROM first_contact_records WHERE id = ?",
            (int(task_id),)).fetchone()
        if row is None:
            return "UNKNOWN"
        new_attempts = (row["followup_attempts"] or 0) + 1
        final_status = "FOLLOWUP_FAILED" if new_attempts >= int(max_attempts) else "WAITING_FOLLOWUP"
        conn.execute(
            "UPDATE first_contact_records SET"
            " status = ?,"
            " followup_attempts = ?,"
            " last_error = ?,"
            " updated_at = ?"
            " WHERE id = ?",
            (final_status, new_attempts, str(error)[:500], now, int(task_id)))
        conn.commit()
        return final_status
    except Exception:
        return "UNKNOWN"


# ─── 查询接口 ───

def get_device_status_history(device_id: str, limit: int = 20):
    """查询设备最近 N 条状态记录"""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM device_status WHERE device_id = ?"
            " ORDER BY recorded_at DESC LIMIT ?",
            (device_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_task_stats(device_id: str = "", hours: int = 24):
    """查询任务统计（可按设备过滤）"""
    try:
        conn = _get_conn()
        cutoff = time.time() - hours * 3600
        if device_id:
            rows = conn.execute(
                "SELECT task_type, status, COUNT(*) as cnt,"
                " AVG(duration_ms) as avg_ms, SUM(replied_count) as total_replied"
                " FROM task_log WHERE device_id = ? AND created_at > ?"
                " GROUP BY task_type, status",
                (device_id, cutoff)).fetchall()
        else:
            rows = conn.execute(
                "SELECT device_id, task_type, status, COUNT(*) as cnt,"
                " AVG(duration_ms) as avg_ms, SUM(replied_count) as total_replied"
                " FROM task_log WHERE created_at > ?"
                " GROUP BY device_id, task_type, status",
                (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_recent_leads(hours: int = 24):
    """查询最近的高意向线索（L3+）"""
    try:
        conn = _get_conn()
        cutoff = time.time() - hours * 3600
        rows = conn.execute(
            "SELECT * FROM reply_log WHERE created_at > ?"
            " AND intent_level IN ('L3', 'L4', 'L5')"
            " ORDER BY created_at DESC",
            (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_db_size():
    """返回数据库文件大小（字节）"""
    try:
        return os.path.getsize(DB_PATH)
    except Exception:
        return 0
