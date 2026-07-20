"""
结构化日志工具 — 统一日志格式，支持按设备/操作/耗时检索
每行日志: 时间 [设备] [模块] action=xxx key=value ...
"""
import time
import logging
from contextlib import contextmanager

logger = logging.getLogger("adb-bridge")

# 是否输出 JSON 格式（通过环境变量 LOG_FORMAT=json 控制）
import os as _os
_LOG_JSON = _os.environ.get("LOG_FORMAT", "").lower() == "json"


def device_log(device_addr: str, action: str, **kwargs):
    """设备操作日志 — 结构化 key=value 格式
    例: device_log("cloud-03", "check_chat", result="replied", count=2, duration_ms=1234)
    """
    parts = [f"action={action}"]
    for k, v in kwargs.items():
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")
    extra = " ".join(parts)
    logger.info("[%s] %s", device_addr, extra)


def device_error(device_addr: str, action: str, error: str, **kwargs):
    """设备错误日志"""
    parts = [f"action={action}", f"error={error}"]
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    logger.error("[%s] %s", device_addr, " ".join(parts))


def system_log(action: str, **kwargs):
    """系统级别日志"""
    parts = [f"action={action}"]
    for k, v in kwargs.items():
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")
    logger.info("[system] %s", " ".join(parts))


@contextmanager
def timed(device_addr: str, action: str):
    """计时上下文管理器 — 自动记录操作耗时
    用法:
        with timed("cloud-03", "add_friend"):
            do_something()
        # 自动输出: [cloud-03] action=add_friend duration_ms=1234 result=ok
    """
    t0 = time.time()
    try:
        yield
        dt = (time.time() - t0) * 1000
        device_log(device_addr, action, duration_ms=dt, result="ok")
    except Exception as e:
        dt = (time.time() - t0) * 1000
        device_error(device_addr, action, str(e)[:100], duration_ms=dt)
        raise
