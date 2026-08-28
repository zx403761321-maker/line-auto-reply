"""monitor-agent 主循环：tail JSONL + 心跳采集 + 有序上传。"""
import json
import logging
import os
import time
import uuid

import collect
from outbox import Outbox
from uploader import Uploader

log = logging.getLogger("monitor-agent")

AGENT_VERSION = "0.1.0"


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.server_id = cfg["server_id"]
        self.agent_version = AGENT_VERSION
        self.agent_instance_id = str(uuid.uuid4())
        self.agent_started_at = time.time()

        self.outbox = Outbox()
        self.uploader = Uploader(cfg)
        self.cpu = collect.CpuSampler(cfg["paths"]["host_root"] + "/proc/stat")

        self._tail_buf = b""
        self._offset = 0

    def run(self):
        log.info("server_id=%s agent_instance_id=%s", self.server_id, self.agent_instance_id)
        self.cpu.percent()  # 预热 CPU 采样基线
        # 从文件末尾开始 tail：历史事件不补传（幂等 + 心跳收敛），避免重放大文件
        self._offset = self._file_size()

        last_stats = last_heartbeat = last_flush = 0.0
        while True:
            now = time.time()
            try:
                if now - last_stats >= self.cfg["interval"]["stats"]:
                    self._tail_events()
                    last_stats = now
                if now - last_heartbeat >= self.cfg["interval"]["heartbeat"]:
                    self._push_heartbeat()
                    last_heartbeat = now
                if now - last_flush >= self.cfg["interval"]["flush"]:
                    self._flush()
                    last_flush = now
            except Exception:
                log.exception("主循环异常")
            time.sleep(1)

    # ── tail JSONL ──
    def _file_size(self):
        try:
            return os.path.getsize(self.cfg["paths"]["events_jsonl"])
        except OSError:
            return 0

    def _tail_events(self):
        p = self.cfg["paths"]["events_jsonl"]
        size = self._file_size()
        if size < self._offset:
            self._offset = 0  # 文件被截断/轮转
            self._tail_buf = b""
        if size <= self._offset:
            return
        try:
            with open(p, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return
        self._tail_buf += data
        lines = self._tail_buf.split(b"\n")
        self._tail_buf = lines.pop()  # 最后一段可能不完整
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line.decode("utf-8"))
                if ev.get("event_type") and ev.get("event_id"):
                    self.outbox.push(ev)
            except (ValueError, UnicodeDecodeError):
                log.warning("跳过非法 JSONL 行: %.80s", line)
        self._offset = size - len(self._tail_buf)

    # ── 心跳 ──
    def _push_heartbeat(self):
        paths = self.cfg["paths"]
        payload = {
            "cpu_percent": self.cpu.percent(),
            "memory_percent": collect.memory_percent(paths["host_root"] + "/proc/meminfo"),
            "disk_percent": collect.disk_percent(paths["host_root"]),
            "docker": collect.docker_status(paths["docker_sock"]),
            "devices": collect.collect_snapshot(paths["sqlite_db"], paths["device_limit"]),
        }
        self.outbox.push(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "heartbeat",
                "ts": time.time(),
                "payload": payload,
            }
        )

    # ── 上传 ──
    def _flush(self):
        events = self.outbox.snapshot()
        if not events:
            return
        body = {
            "server_id": self.server_id,
            "agent_version": self.agent_version,
            "agent_instance_id": self.agent_instance_id,
            "agent_started_at": self.agent_started_at,
            "pending_count": len(self.outbox),
            "events": events,
        }
        try:
            self.uploader.upload(body)
            n = len(events)
            self.outbox.clear()
            log.info("上传成功 %d 条事件", n)
        except Exception:
            log.warning("上传失败，%d 条事件保留待重试", len(events))
