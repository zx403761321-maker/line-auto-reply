"""
LINE 自动回复循环 - 每台串行 - 添加时段跳过该台
"""
import time, requests
from datetime import datetime

BRIDGE = "http://127.0.0.1:8899"
DEVICES = ["cloud-02", "cloud-03", "cloud-04", "cloud-05"]
ADD_SLOTS = {"cloud-01": 9, "cloud-02": 11, "cloud-03": 13, "cloud-04": 15, "cloud-05": 17}

while True:
    now = datetime.now()
    hour, minute = now.hour, now.minute

    if hour >= 23 or hour < 8:
        print(f"[{now.strftime('%H:%M')}] 休眠")
        time.sleep(60)
        continue

    for dev in DEVICES:
        slot = ADD_SLOTS.get(dev)
        if slot and hour == slot and minute < 60:
            print(f"[{now.strftime('%H:%M')}] {dev} 添加中跳过")
            time.sleep(5)
            continue
        try:
            r = requests.post(f"{BRIDGE}/line/check-latest-chat?device={dev}",
                              json={}, timeout=90)
            data = r.json()
            if data.get("replied"):
                print(f"[{now.strftime('%H:%M')}] {dev} ✅ 已回复")
            else:
                print(f"[{now.strftime('%H:%M')}] {dev} {data.get('reason','?')}")
        except Exception as e:
            print(f"[{now.strftime('%H:%M')}] {dev} ❌ {e}")
        time.sleep(60)

    time.sleep(10)
