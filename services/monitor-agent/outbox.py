"""有序 outbox + 合并策略。"""
from collections import deque


class Outbox:
    """pending 策略：
    - heartbeat：最新覆盖（只保留最新一条）
    - device_status：按 device_id 合并（P1 仅 online 维度；P2 加 dimension）
    - 其他类型（task_result/error_event）：不可丢，仅追加
    """

    def __init__(self):
        self._events = deque()

    def push(self, event):
        etype = event.get("event_type")
        if etype == "heartbeat":
            self._events = deque(
                e for e in self._events if e.get("event_type") != "heartbeat"
            )
        elif etype == "device_status":
            dev = (event.get("payload") or {}).get("device_id")
            self._events = deque(
                e
                for e in self._events
                if not (
                    e.get("event_type") == "device_status"
                    and (e.get("payload") or {}).get("device_id") == dev
                )
            )
        self._events.append(event)

    def snapshot(self):
        return list(self._events)

    def clear(self):
        self._events.clear()

    def __len__(self):
        return len(self._events)

    def __bool__(self):
        return bool(self._events)
