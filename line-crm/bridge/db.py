"""
SQLite 持久化模块 — 设备状态 + 任务记录 + AI 回复日志
每条记录自动过期清理（默认保留 30 天）
"""
import sqlite3
import time
import os
import threading

DB_PATH = os.environ.get("DB_PATH", "/app/data/bridge.db")

_conn_local = threading.local()


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
