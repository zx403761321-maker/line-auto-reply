"""加载 config/monitor.yaml（默认值 + 必填校验）。"""
import os

import yaml

DEFAULTS = {
    "monitor_url": "http://127.0.0.1:8000",
    "ingest_token": "",
    "verify_tls": True,
    "interval": {"heartbeat": 60, "flush": 5, "stats": 10},
    "paths": {
        "events_jsonl": "/data/state/monitor_events.jsonl",
        "sqlite_db": "/data/bridge.db",
        "device_limit": "/data/state/device_limit_status.json",
        "host_root": "/host",
        "docker_sock": "/var/run/docker.sock",
    },
}

CONFIG_PATH = os.environ.get("MONITOR_CONFIG", "/config/monitor.yaml")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    cfg = _deep_merge(DEFAULTS, data)
    if not cfg.get("server_id"):
        raise RuntimeError(f"monitor.yaml 缺少 server_id（{CONFIG_PATH}）")
    return cfg
