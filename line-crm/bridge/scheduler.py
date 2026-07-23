"""内置定时器 — 替代 crontab"""
import threading, subprocess, time
from datetime import datetime

SCHEDULE = [
    ("08:00", "make -C /root/line-crm start"),
    ("09:30", "bash /root/line-crm/scripts/report.sh"),
]


def _run():
    last_run = {}
    while True:
        now = datetime.now()
        current = now.strftime("%H:%M")
        for at, cmd in SCHEDULE:
            if current == at and last_run.get(at) != now.strftime("%Y-%m-%d"):
                last_run[at] = now.strftime("%Y-%m-%d")
                try:
                    subprocess.run(cmd, shell=True, timeout=600)
                except Exception:
                    pass
        time.sleep(30)


def start():
    t = threading.Thread(target=_run, daemon=True)
    t.start()
