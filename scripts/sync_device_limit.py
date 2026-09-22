#!/usr/bin/env python3
# 把 adb-bridge 的 SQLite 冷却/判死状态同步成 monitor-agent 要读的 device_limit_status.json
# 只读 bridge.db，不改业务逻辑；写入 /root/line-crm/data/state/device_limit_status.json
import json, sqlite3, os
from datetime import datetime, timezone, timedelta

DB  = "/root/line-crm/data/bridge.db"
OUT = "/root/line-crm/data/state/device_limit_status.json"
TZ  = timezone(timedelta(hours=8))

def iso(epoch):
    if not epoch:
        return None
    return datetime.fromtimestamp(float(epoch), tz=TZ).strftime("%Y-%m-%d")

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT device_id, status, cooldown_until, search_limit_count, cooldown_reason "
    "FROM account_status").fetchall()
conn.close()

out = {}
for r in rows:
    st, cnt = r["status"], r["search_limit_count"] or 0
    dev = {"status": "active", "cooldown_count": 0,
           "updated": datetime.now(TZ).isoformat()}
    if st in ("dead", "banned"):
        dev.update(status="needs_replacement", cooldown_count=cnt,
                   cooldown_until=iso(r["cooldown_until"]),
                   reason=r["cooldown_reason"] or "已判死")
    elif st in ("cooldown", "search_limit"):
        dev.update(status="cooling_down", cooldown_count=cnt,
                   cooldown_until=iso(r["cooldown_until"]),
                   reason=r["cooldown_reason"] or "搜索次数达上限")
    out[r["device_id"]] = dev

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
