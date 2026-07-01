"""
ADB 操作封装
支持：USB直连 / 网络ADB / 云手机
"""
import subprocess
import time
import logging
from typing import Optional

log = logging.getLogger(__name__)

ADB_TIMEOUT = 15


def adb_command(device_addr: str, *args, timeout: int = ADB_TIMEOUT):
    """
    执行 ADB 命令。

    Args:
        device_addr: 设备地址，如 "your-cloud-phone-ip:499" 或 "emulator-5554"
        *args: ADB 参数，如 "shell", "input", "tap", "100", "200"
        timeout: 超时秒数

    Returns:
        subprocess.CompletedProcess
    """
    cmd = ["adb", "-s", device_addr] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def adb_shell(device_addr: str, command: str, timeout: int = ADB_TIMEOUT) -> str:
    """执行 shell 命令并返回 stdout"""
    result = adb_command(device_addr, "shell", command, timeout=timeout)
    return result.stdout.strip()


def connect(host: str, port: int = 5555, timeout: int = 10) -> bool:
    """
    连接网络 ADB 设备。

    Returns:
        True 如果连接成功
    """
    addr = f"{host}:{port}"
    result = subprocess.run(
        ["adb", "connect", addr],
        capture_output=True, text=True, timeout=timeout
    )
    output = result.stdout + result.stderr
    if "connected" in output.lower() or "already connected" in output.lower():
        log.info(f"ADB 连接成功: {addr}")
        return True
    log.warning(f"ADB 连接失败: {addr} → {output.strip()}")
    return False


def disconnect(host: str, port: int = 5555):
    """断开网络 ADB 设备"""
    addr = f"{host}:{port}"
    subprocess.run(["adb", "disconnect", addr], capture_output=True, timeout=5)


def is_connected(device_addr: str) -> bool:
    """检查设备是否在线"""
    result = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=5
    )
    for line in result.stdout.split("\n"):
        if device_addr in line and "\tdevice" in line:
            return True
    return False


def tap(device_addr: str, x: int, y: int):
    """点击屏幕坐标"""
    adb_command(device_addr, "shell", "input", "tap", str(x), str(y))


def swipe(device_addr: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
    """滑动"""
    adb_command(device_addr, "shell", "input", "swipe",
                str(x1), str(y1), str(x2), str(y2), str(duration_ms))


def input_text_adbkeyboard(device_addr: str, text: str):
    """
    用 ADBKeyboard 输入中文。
    前提：手机已安装 ADBKeyboard 并设为默认输入法。
    """
    adb_command(device_addr, "shell", "ime", "set",
                "com.android.adbkeyboard/.AdbIME")
    time.sleep(0.3)
    adb_command(device_addr, "shell", "am", "broadcast", "-a",
                "ADB_INPUT_TEXT", "--es", "msg", text)
    time.sleep(0.5)


def input_text_ime(device_addr: str, text: str):
    """
    用当前输入法输入（仅限 ASCII）。
    非 ASCII 文字用 ADBKeyboard 替代。
    """
    # 转义特殊字符
    text_escaped = text.replace(" ", "%s").replace("&", "\\&")
    adb_command(device_addr, "shell", "input", "text", text_escaped)


def start_app(device_addr: str, package: str, activity: Optional[str] = None):
    """启动应用"""
    if activity:
        adb_command(device_addr, "shell", "am", "start", "-n",
                    f"{package}/{activity}")
    else:
        adb_command(device_addr, "shell", "monkey", "-p", package,
                    "-c", "android.intent.category.LAUNCHER", "1")


def press_key(device_addr: str, keycode: str):
    """按系统键"""
    adb_command(device_addr, "shell", "input", "keyevent", keycode)


def get_device_info(device_addr: str) -> dict:
    """获取设备基本信息"""
    info = {}
    try:
        info["model"] = adb_shell(device_addr, "getprop ro.product.model")
        info["brand"] = adb_shell(device_addr, "getprop ro.product.brand")
        info["android_version"] = adb_shell(device_addr, "getprop ro.build.version.release")
        info["sdk"] = adb_shell(device_addr, "getprop ro.build.version.sdk")
    except:
        pass
    return info


def check_uiautomator(device_addr: str) -> bool:
    """检查设备上是否安装了 uiautomator2 agent"""
    try:
        result = adb_shell(device_addr, "pm list packages | grep atx")
        return "atx" in result.lower()
    except:
        return False
