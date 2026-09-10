"""主机指标 + docker 状态 + 设备快照采集。"""
import json
import shutil
import socket
import sqlite3
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))  # Asia/Taipei 无 DST，固定 UTC+8


# ── CPU ──────────────────────────────────────────────────────────
class CpuSampler:
    """读 /host/proc/stat，两次采样间计算 busy 占比。"""

    def __init__(self, stat_path="/host/proc/stat"):
        self.stat_path = stat_path
        self._last = None

    def _read(self):
        try:
            with open(self.stat_path, "r") as f:
                for line in f:
                    if line.startswith("cpu "):
                        return [int(x) for x in line.split()[1:]]
        except Exception:
            return None
        return None

    def percent(self):
        now = self._read()
        if now is None:
            return None
        if self._last is None or len(self._last) != len(now):
            self._last = now
            return None
        idle = (now[3] - self._last[3]) + (now[4] - self._last[4])  # idle + iowait
        total = sum(now) - sum(self._last)
        self._last = now
        if total <= 0:
            return None
        return round((1 - idle / total) * 100, 1)


# ── 内存 ────────────────────────────────────────────────────────
def memory_percent(meminfo_path="/host/proc/meminfo"):
    total = avail = None
    try:
        with open(meminfo_path, "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
    except Exception:
        return None
    if not total or avail is None:
        return None
    return round((1 - avail / total) * 100, 1)


# ── 磁盘 ────────────────────────────────────────────────────────
def disk_percent(host_root="/host"):
    try:
        u = shutil.disk_usage(host_root)
        return round((u.used / u.total) * 100, 1)
    except Exception:
        return None


# ── docker（只读：仅 GET /containers/json，等价 docker ps）────────
def docker_status(sock_path="/var/run/docker.sock"):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(sock_path)
        req = (
            b"GET /containers/json?all=1 HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n\r\n"
        )
        s.sendall(req)
        resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        s.close()
        body = resp.split(b"\r\n\r\n", 1)[1]
        containers = json.loads(body)
        up = sum(1 for c in containers if c.get("State") == "running")
        return {"containers": len(containers), "up": up}
    except Exception:
        return None


# ── 设备稳定快照（bridge.db + device_limit_status.json）───────────
def _parse_epoch(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    try:
        if "T" in s or " " in s:
            dt = datetime.fromisoformat(s)
        else:
            dt = datetime.strptime(s, "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TAIPEI)
        return dt.timestamp()
    except Exception:
        return None


def collect_snapshot(sqlite_db, device_limit_path):
    devices = {}

    # 1. SQLite account_status：完整设备列表 + 账号指标
    try:
        conn = sqlite3.connect(f"file:{sqlite_db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT device_id, daily_success, daily_fail, risk_score, last_task_at, daily_date "
            "FROM account_status"
        ).fetchall()
        conn.close()
        for r in rows:
            devices[r["device_id"]] = {
                "device_id": r["device_id"],
                "today_success": r["daily_success"] or 0,
                "today_fail": r["daily_fail"] or 0,
                "risk_score": r["risk_score"] or 0,
                "last_task_at": r["last_task_at"],
                "daily_date": r["daily_date"],
                "cooldown_count": 0,
                "cooldown_until": None,
                "needs_replacement": False,
            }
    except Exception:
        pass  # 只读库可能被写锁占用，静默降级，下次 heartbeat 再试

    # 2. device_limit_status.json：cooldown_count / cooldown_until / needs_replacement
    try:
        with open(device_limit_path, "r", encoding="utf-8") as f:
            limit = json.load(f)
        for dev_id, info in limit.items():
            d = devices.setdefault(
                dev_id,
                {
                    "device_id": dev_id,
                    "today_success": 0,
                    "today_fail": 0,
                    "risk_score": 0,
                    "last_task_at": None,
                    "daily_date": None,
                },
            )
            d["cooldown_count"] = info.get("cooldown_count", 0)
            d["cooldown_until"] = _parse_epoch(info.get("cooldown_until"))
            d["needs_replacement"] = info.get("status") == "needs_replacement"
    except Exception:
        pass

    return list(devices.values())
