"""
ADB Bridge v2 — 多设备支持
每台手机通过 ?device=<id> 路由，默认设备为云手机
"""
import subprocess, json, time, re, base64, logging, requests, os
import uiautomator2 as u2
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [adb-bridge] %(message)s")
logger = logging.getLogger(__name__)

ADB_CMD = "/usr/local/bin/adb"
ADB_TIMEOUT = 15
LINE_PACKAGE = "jp.naver.line.android"

# ─── 设备注册表 ───
DEVICES_FILE = "/app/data/devices.json"
DEVICES = {
    "cloud-01": {
        "addr": "39.109.41.52:499",
        "type": "cloud",
        "label": "云手机-OPPO",
    }
}
# 从文件加载持久化设备
if os.path.exists(DEVICES_FILE):
    try:
        with open(DEVICES_FILE) as f:
            saved = json.load(f)
            for dev_id, info in saved.items():
                if True:  # 文件数据覆盖
                    DEVICES[dev_id] = info
        logger.info("从文件加载设备: %s", list(saved.keys()))
    except:
        pass

# ─── uiautomator2 连接缓存 ───
_u2_cache: dict[str, object] = {}


def get_device_addr(device_id: str = None) -> str:
    """解析设备 ID → ADB 地址"""
    if device_id and device_id in DEVICES:
        return DEVICES[device_id]["addr"]
    if device_id:
        # 可能是直接传的 host:port
        if ":" in device_id:
            return device_id
    # 默认返回第一个设备
    first = next(iter(DEVICES.values()))
    return first["addr"]


import threading
_device_locks = {}
def get_device_lock(addr):
    if addr not in _device_locks:
        _device_locks[addr] = threading.Lock()
    return _device_locks[addr]

def _resolve_device() -> str:
    """从请求参数解析目标设备 ADB 地址"""
    device_id = request.args.get("device") or request.get_json(silent=True) and request.get_json().get("device")
    if not device_id:
        device_id = request.args.get("device")
    return get_device_addr(device_id)


def get_u2(device_addr: str):
    """获取 uiautomator2 连接（带缓存）"""
    if device_addr in _u2_cache:
        try:
            _u2_cache[device_addr].info  # 测试连接是否存活
            return _u2_cache[device_addr]
        except:
            pass
    _u2_cache[device_addr] = u2.connect(device_addr)
    return _u2_cache[device_addr]


def adb(device_addr: str, *args, timeout=None):
    """执行 ADB 命令"""
    cmd = [ADB_CMD, "-s", device_addr] + list(args)
    logger.info("ADB[%s]: %s", device_addr, " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout or ADB_TIMEOUT)
        return {"ok": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def adb_raw(device_addr: str, *args, timeout=None):
    """执行 ADB 命令（二进制输出）"""
    cmd = [ADB_CMD, "-s", device_addr] + list(args)
    logger.info("ADB_RAW[%s]: %s", device_addr, " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True,
                                timeout=timeout or ADB_TIMEOUT)
        return {"ok": result.returncode == 0, "stdout_bytes": result.stdout,
                "stderr": result.stderr.decode(errors="replace").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout_bytes": b"", "stderr": "timeout"}
    except Exception as e:
        return {"ok": False, "stdout_bytes": b"", "stderr": str(e)}


def ensure_connected(device_addr: str) -> bool:
    """确保 ADB 连接"""
    r = adb(device_addr, "shell", "echo", "ok", timeout=5)
    if r["ok"] and "ok" in r["stdout"]:
        return True
    logger.warning("ADB 断开 [%s]，尝试重连...", device_addr)
    subprocess.run([ADB_CMD, "connect", device_addr], capture_output=True, timeout=5)
    time.sleep(2)
    r2 = adb(device_addr, "shell", "echo", "ok", timeout=5)
    return r2["ok"] and "ok" in r2["stdout"]


def ui_find(device_addr: str, text_contains: str, timeout=3):
    """UIAutomator dump → 查找包含指定文字的元素坐标"""
    for _ in range(timeout):
        subprocess.run(
            [ADB_CMD, "-s", device_addr, "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
            capture_output=True, timeout=10
        )
        r = adb_raw(device_addr, "exec-out", "cat", "/sdcard/ui.xml")
        if not r["ok"]:
            time.sleep(1)
            continue
        xml = r["stdout_bytes"].decode("utf-8", errors="replace")

        # 优先找 clickable 元素
        m = re.search(
            rf'text="[^"]*{re.escape(text_contains)}[^"]*"[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml
        )
        if m:
            cx = (int(m.group(1)) + int(m.group(3))) // 2
            cy = (int(m.group(2)) + int(m.group(4))) // 2
            logger.info("UI_FIND[%s] [%s] → (%d, %d)", device_addr, text_contains, cx, cy)
            return (cx, cy)

        # 退一步：不要求 clickable
        m = re.search(
            rf'text="[^"]*{re.escape(text_contains)}[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml
        )
        if m:
            cx = (int(m.group(1)) + int(m.group(3))) // 2
            cy = (int(m.group(2)) + int(m.group(4))) // 2
            logger.info("UI_FIND(any)[%s] [%s] → (%d, %d)", device_addr, text_contains, cx, cy)
            return (cx, cy)

        time.sleep(1)
    return None


def ui_tap(device_addr: str, text: str, timeout=3):
    """找到包含 text 的元素并点击"""
    pos = ui_find(device_addr, text, timeout)
    if pos:
        adb(device_addr, "shell", "input", "tap", str(pos[0]), str(pos[1]))
        return True
    logger.warning("UI_TAP[%s] [%s] not found", device_addr, text)
    return False


def type_text(device_addr: str, text: str):
    """通过 uiautomator2 输入文字（支持中文/emoji）
    优先找聊天输入框，其次找任意已聚焦的 EditText"""
    d = get_u2(device_addr)
    # 方式1: LINE 聊天输入框
    el = d(resourceId="jp.naver.line.android:id/chat_ui_message_edit")
    if el.exists:
        el.click()
        time.sleep(0.3)
        el.set_text(text)
        logger.info("TYPED[%s](chat): %s", device_addr, text[:50])
        return
    # 方式2: 已聚焦的编辑框（搜索框等）
    el = d(focused=True, className="android.widget.EditText")
    if el.exists:
        el.set_text(text)
        logger.info("TYPED[%s](search): %s", device_addr, text[:50])
        return
    # 方式3: 任意 EditText
    el = d(className="android.widget.EditText")
    if el.exists:
        el.click()
        time.sleep(0.3)
        el.set_text(text)
        logger.info("TYPED[%s](edit): %s", device_addr, text[:50])
        return
    # 回退
    d.send_keys(text)
    logger.info("TYPED[%s](fallback): %s", device_addr, text[:50])


# ═══════════════════════════════════════════
# 设备管理 API
# ═══════════════════════════════════════════

@app.route("/devices", methods=["GET"])
def list_devices():
    """列出所有已注册设备及其连接状态"""
    result = {}
    for dev_id, info in DEVICES.items():
        addr = info["addr"]
        connected = ensure_connected(addr)
        result[dev_id] = {
            **info,
            "connected": connected,
            "addr": addr
        }
    return jsonify({"ok": True, "devices": result})


@app.route("/device/register", methods=["POST"])
def register_device():
    """
    注册新设备（运行时添加，不需重启）
    {"device_id": "real-01", "addr": "100.1.2.3:5555", "type": "real", "label": "红米01"}
    """
    data = request.get_json()
    dev_id = data["device_id"]
    addr = data["addr"]

    DEVICES[dev_id] = {
        "addr": addr,
        "type": data.get("type", "real"),
        "label": data.get("label", dev_id),
    }

    # 尝试连接
    subprocess.run([ADB_CMD, "connect", addr], capture_output=True, timeout=5)
    time.sleep(2)
    connected = ensure_connected(addr)

    logger.info("设备注册: %s → %s (connected=%s)", dev_id, addr, connected)
    # 持久化到文件
    try:
        os.makedirs(os.path.dirname(DEVICES_FILE), exist_ok=True)
        with open(DEVICES_FILE, "w") as f:
            json.dump(DEVICES, f, ensure_ascii=False, indent=2)
    except:
        pass
    return jsonify({"ok": connected, "device_id": dev_id, "addr": addr, "connected": connected})


@app.route("/device/<device_id>/remove", methods=["DELETE"])
def remove_device(device_id: str):
    """移除设备"""
    if device_id in DEVICES:
        addr = DEVICES[device_id]["addr"]
        subprocess.run([ADB_CMD, "disconnect", addr], capture_output=True, timeout=5)
        del DEVICES[device_id]
        _u2_cache.pop(addr, None)
        return jsonify({"ok": True, "removed": device_id})
    return jsonify({"ok": False, "error": "device not found"}), 404


# ═══════════════════════════════════════════
# 健康检查 & 设备信息
# ═══════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    device_addr = _resolve_device()
    connected = ensure_connected(device_addr)
    status = "live" if connected else "device-unreachable"
    code = 200 if connected else 503
    return jsonify({"ok": connected, "status": status, "device": device_addr}), code


@app.route("/device/info", methods=["GET"])
def device_info():
    device_addr = _resolve_device()
    if not ensure_connected(device_addr):
        return jsonify({"error": "设备不可达"}), 503
    model = adb(device_addr, "shell", "getprop", "ro.product.model")
    android = adb(device_addr, "shell", "getprop", "ro.build.version.release")
    battery = adb(device_addr, "shell", "dumpsys", "battery")
    level = "?"
    for line in battery["stdout"].split("\n"):
        if "level:" in line:
            level = line.split(":")[1].strip()
    return jsonify({
        "device": device_addr,
        "model": model["stdout"],
        "android": android["stdout"],
        "battery": level,
    })


# ═══════════════════════════════════════════
# LINE APP 基础操作
# ═══════════════════════════════════════════

@app.route("/line/open", methods=["POST"])
def line_open():
    device_addr = _resolve_device()
    ensure_connected(device_addr)
    r = adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE,
            "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(2)
    return jsonify(r)


@app.route("/line/close", methods=["POST"])
def line_close():
    device_addr = _resolve_device()
    ensure_connected(device_addr)
    r = adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
    return jsonify(r)


@app.route("/line/screenshot", methods=["GET"])
def line_screenshot():
    device_addr = _resolve_device()
    ensure_connected(device_addr)
    r = adb_raw(device_addr, "exec-out", "screencap", "-p")
    if r["ok"]:
        img_b64 = base64.b64encode(r["stdout_bytes"]).decode("ascii")
        return jsonify({"ok": True, "image_base64": img_b64})
    return jsonify(r), 500


@app.route("/line/tap", methods=["POST"])
def line_tap():
    device_addr = _resolve_device()
    data = request.get_json()
    ensure_connected(device_addr)
    r = adb(device_addr, "shell", "input", "tap", str(data["x"]), str(data["y"]))
    return jsonify(r)


@app.route("/line/swipe", methods=["POST"])
def line_swipe():
    device_addr = _resolve_device()
    data = request.get_json()
    ensure_connected(device_addr)
    r = adb(device_addr, "shell", "input", "swipe",
            str(data["x1"]), str(data["y1"]),
            str(data["x2"]), str(data["y2"]))
    return jsonify(r)


@app.route("/line/type", methods=["POST"])
def line_type():
    device_addr = _resolve_device()
    data = request.get_json()
    ensure_connected(device_addr)
    type_text(device_addr, data["text"])
    return jsonify({"ok": True})


@app.route("/line/send-key", methods=["POST"])
def line_send_key():
    device_addr = _resolve_device()
    data = request.get_json()
    ensure_connected(device_addr)
    r = adb(device_addr, "shell", "input", "keyevent", str(data["key"]))
    return jsonify(r)


# ═══════════════════════════════════════════
# LINE 高级操作
# ═══════════════════════════════════════════

@app.route("/line/add-friend-by-id", methods=["POST"])
def line_add_friend_by_id():
    """通过 LINE ID 添加好友 — 用户实测路径，固定坐标
    路径: 主页(588,102)添加好友 → (74,306)搜索 → ID标签 → 输入 → 搜索 → 添加
    """
    data = request.get_json()
    line_id = data["line_id"]
    message = data.get("message", "你好，我是貸款顧問")
    device_addr = _resolve_device()
    lock = get_device_lock(device_addr)
    if not lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "device_busy"})
    ensure_connected(device_addr)
    steps = []

    # 1. 强制重启 LINE → u2找主页Tab精准点击
    adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
    time.sleep(2)
    adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(6)
    u2home = get_u2(device_addr)
    home_el = u2home(text="主页")
    if home_el.exists(timeout=2):
        b = home_el.info['bounds']
        adb(device_addr, "shell", "input", "tap", str((b['left']+b['right'])//2), str((b['top']+b['bottom'])//2))
    else:
        adb(device_addr, "shell", "input", "tap", "72", "1128")
    time.sleep(4)
    steps.append("goto_home")

    # 2. 首頁右上角➕ @(588,102)
    adb(device_addr, "shell", "input", "tap", "588", "102")
    time.sleep(4)
    steps.append("tap_add_friend_btn")

    # 2b. 验证到了添加好友页（有「搜索」才算对）
    u2check = get_u2(device_addr)
    if not u2check(description="搜索").exists(timeout=3) and not u2check(text="搜索").exists(timeout=2):
        logger.info("点➕后页面不对，重启")
        steps.append("bad_page")
        adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
        time.sleep(2)
        adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(5)
        adb(device_addr, "shell", "input", "tap", "72", "1128"); time.sleep(2)
        u2r = get_u2(device_addr)
        ab = u2r(description="添加好友")
        if ab.exists(timeout=2): ab.click()
        else: adb(device_addr, "shell", "input", "tap", "588", "102")
        time.sleep(3)
        steps.append("retry_plus")

    # 3. 放大镜搜索 @(600,271)
    adb(device_addr, "shell", "input", "tap", "600", "271")
    time.sleep(3)
    steps.append("tap_search_icon")

    # 4. 验证页面：必须看到「ID」标签才继续，否则重启重来
    xd3 = get_u2(device_addr)
    if not xd3(text="ID").exists(timeout=2):
        logger.info("找不到ID标签，页面错误，重启重试")
        steps.append("page_error")
        adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
        time.sleep(2)
        adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(5)
        adb(device_addr, "shell", "input", "tap", "72", "1128"); time.sleep(2)
        adb(device_addr, "shell", "input", "tap", "588", "102"); time.sleep(3)
        adb(device_addr, "shell", "input", "tap", "600", "271"); time.sleep(3)
        steps.append("retry_nav")
    # 切ID，输ID
    adb(device_addr, "shell", "input", "tap", "78", "248")
    time.sleep(0.5)
    steps.append("tab_id")

    # 5. 清空输入框 @(336,356) 并输入 LINE ID
    adb(device_addr, "shell", "input", "tap", "336", "356")
    time.sleep(0.3)
    for _ in range(5):
        adb(device_addr, "shell", "input", "keyevent", "KEYCODE_DEL")
        time.sleep(0.05)
    type_text(device_addr, line_id)
    time.sleep(0.5)
    steps.append("type_id")

    # 6. 点「搜索」按钮 @(650,356)
    adb(device_addr, "shell", "input", "tap", "650", "356")
    time.sleep(3)
    steps.append("search")

    # 7. 检查结果（用 u2 不用 adb dump）
    xd = get_u2(device_addr)
    xd_xml = xd.dump_hierarchy()
    if "搜索次数已达上限" in xd_xml or "暂时不能使用ID搜索" in xd_xml:
        steps.append("search_limit")
        lock.release()
        return jsonify({"ok": False, "steps": steps, "line_id": line_id,
                        "error": "search_limit"})
    if "未找到" in xd_xml:
        steps.append("no_result")
        lock.release()
        return jsonify({"ok": False, "steps": steps, "line_id": line_id,
                        "error": "未找到该用户"})
    # 点击「添加」
    if not ui_tap(device_addr, "添加", timeout=2):
        adb(device_addr, "shell", "input", "tap", "360", "834")
        time.sleep(1)
        steps.append("tap_add_xy")
    else:
        steps.append("tap_add")
    time.sleep(1.5)

    # 8. 二次确认
    ui_tap(device_addr, "添加", timeout=1)
    time.sleep(0.5)
    steps.append("confirm_add")

    # 写映射{LINE_ID: LINE_ID}
    try:
        cj = {}
        if os.path.exists("/app/data/contact_map.json"):
            with open("/app/data/contact_map.json") as f: cj = json.load(f)
        cj[line_id] = line_id
        os.makedirs("/app/data", exist_ok=True)
        with open("/app/data/contact_map.json","w") as f: json.dump(cj,f,ensure_ascii=False)
    except: pass

    # 9. 「添加」按钮已变成「聊天」→ 点进去发招呼
    time.sleep(2)
    d = get_u2(device_addr)
    # 同时用 text 和 description 找「聊天」按钮
    chat_btn = d(text="聊天")
    if not chat_btn.exists(timeout=1):
        chat_btn = d(description="聊天")
    if not chat_btn.exists(timeout=1):
        chat_btn = d(descriptionContains="聊天")
    if chat_btn.exists(timeout=2):
        chat_btn.click()
        time.sleep(2)
        # 判断是否进入聊天页：找输入框
        inp = d(description="输入消息")
        if inp.exists(timeout=2):
            inp.click()
            time.sleep(0.3)
            type_text(device_addr, message)
            time.sleep(0.5)
            send_btn = d(description="发送")
            if send_btn.exists(timeout=3):
                send_btn.click()
                time.sleep(1)
                steps.append("greeted")

                # 10. 改好友名字为LINE ID
                renamed_ok = False
                time.sleep(2)
                # 用u2找顶部最宽可点元素=名字条
                for el in d(clickable=True):
                    try:
                        b = el.info.get("bounds",{})
                        w = b.get("right",0)-b.get("left",0)
                        cy = (b.get("top",0)+b.get("bottom",0))//2
                        if cy < 150 and w > 200:
                            nx = b["left"] + int(w * 0.7)
                            adb(device_addr, "shell", "input", "tap", str(nx), str(cy))
                            time.sleep(2)
                            break
                    except: pass
                _u2_cache.pop(device_addr, None)
                xd3 = u2.connect(device_addr)
                xd_xml = xd3.dump_hierarchy()
                if "修改名字" in xd_xml:
                    m = re.search(r"(?:text|content-desc)=\"修改名字\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", xd_xml)
                    if m:
                        cx = (int(m.group(1)) + int(m.group(3))) // 2
                        cy = (int(m.group(2)) + int(m.group(4))) // 2
                        adb(device_addr, "shell", "input", "tap", str(cx), str(cy))
                        time.sleep(1)
                        type_text(device_addr, line_id)
                        time.sleep(0.5)
                        adb(device_addr, "shell", "input", "tap", "360", "1122")
                        time.sleep(1)
                        renamed_ok = True
                        steps.append("renamed")
                if not renamed_ok:
                    steps.append("rename_fail")

                if not renamed_ok:
                    steps.append("rename_fail")
            else:
                steps.append("send_fail")
        else:
            steps.append("chat_page_fail")
    else:
        steps.append("no_chat_yet")

    lock.release()
    return jsonify({"ok": True, "steps": steps, "line_id": line_id, "device": device_addr})


@app.route("/line/check-latest-chat", methods=["POST"])
def check_latest_chat():
    """自动回复：找绿色未读数字 → 进聊天 → 读消息 → 回复 → 发送"""
    device_addr = _resolve_device()
    lock = get_device_lock(device_addr)
    if not lock.acquire(blocking=False):
        return jsonify({"ok": True, "replied": False, "reason": "device_busy"})
    ensure_connected(device_addr)

    # 1. 重启LINE → 关弹窗
    adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
    time.sleep(1)
    adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    # 关掉可能的「未读消息」弹窗
    d_pop = get_u2(device_addr)
    if d_pop(text="未读消息").exists(timeout=1) or d_pop(text="接收时间").exists(timeout=1):
        adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)

    # 2. 看底部聊天Tab有无红数字
    import re
    if device_addr in _u2_cache:
        del _u2_cache[device_addr]
    d = get_u2(device_addr)
    xml = d.dump_hierarchy()
    has_unread = False
    # 底部(y>1050)有纯数字=红badge
    for m in re.finditer(r'text=\"(\d+)\".*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', xml):
        num, x1, y1, x2, y2 = m.group(1), *map(int, m.groups()[1:])
        cy = (y1+y2)//2
        if cy > 1050 and int(num) < 9999000:
            has_unread = True
            logger.info("红数字: [%s] @y=%d", num, cy)
            break
    # 也检查desc方式
    chat_tab = d(descriptionContains="聊天选项")
    if not has_unread and chat_tab.exists(timeout=2):
        desc = chat_tab.info.get('contentDescription', '')
        has_unread = '新项目' in desc
        logger.info("聊天Tab desc: %s", desc)
    if not has_unread:
        lock.release()
        return jsonify({"ok": True, "replied": False, "reason": "no_unread"})

    # 3. 点底部聊天Tab死坐标
    adb(device_addr, "shell", "input", "tap", "216", "1151")
    time.sleep(5)

    # ─── 自动滚屏扫描绿数字：逐屏上滑直到找到未读或滚到顶 ───
    badges = []
    MAX_SCROLLS = 10  # 最多滚10屏，确保覆盖长列表
    prev_xml = ""
    for scroll_i in range(MAX_SCROLLS):
        _u2_cache.pop(device_addr, None)
        d = u2.connect(device_addr)
        xml = d.dump_hierarchy()

        # 如果在好友页，点「聊天」切回
        if "好友列表" in xml:
            adb(device_addr, "shell", "input", "tap", "68", "101")
            time.sleep(2)
            continue

        # 扫描当前屏绿数字
        for m in re.finditer(r'text=\"(\d+)\".*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', xml):
            num, x1, y1, x2, y2 = m.group(1), *map(int, m.groups()[1:])
            cx, cy = (x1+x2)//2, (y1+y2)//2
            if cx > 500 and 400 < cy < 1050 and 1 <= int(num) <= 999:
                badges.append((cy, cx, int(num)))
                logger.info("绿数字: [%s] @(%d,%d) scroll=%d", num, cx, cy, scroll_i)

        if badges:
            break

        # 没找到 → 上滑翻屏
        # 如果 xml 跟上一屏一样，说明滚到头了
        if xml == prev_xml and scroll_i > 0:
            logger.info("聊天列表已滚到底/顶，停止扫描")
            break
        prev_xml = xml

        logger.info("第%d屏无绿数字，继续上滑...", scroll_i + 1)
        adb(device_addr, "shell", "input", "swipe", "360", "1000", "360", "300", "600")
        time.sleep(1.0)

    # 去重排序
    seen = set()
    unique_badges = []
    for b in sorted(badges):
        key = (b[0]//15, b[1]//15)
        if key not in seen:
            seen.add(key)
            unique_badges.append(b)
    badges = unique_badges

    if not badges:
        # 可能是页面加载慢了或进了好友页，重试一次
        logger.info("无绿数字，重试...")
        adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
        time.sleep(1)
        adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE,
            "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(5)
        adb(device_addr, "shell", "input", "tap", "216", "1151")
        time.sleep(5)
        # 重试时也多滚几屏
        prev_xml2 = ""
        for scroll_i in range(MAX_SCROLLS):
            _u2_cache.pop(device_addr, None)
            d = u2.connect(device_addr)
            xml = d.dump_hierarchy()
            if "好友列表" in xml:
                adb(device_addr, "shell", "input", "tap", "68", "101")
                time.sleep(2)
                continue
            for m in re.finditer(r'text=\"(\d+)\".*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', xml):
                num, x1, y1, x2, y2 = m.group(1), *map(int, m.groups()[1:])
                cx, cy = (x1+x2)//2, (y1+y2)//2
                if cx > 500 and 400 < cy < 1050 and 1 <= int(num) <= 999:
                    badges.append((cy, cx, int(num)))
                    logger.info("重试绿数字: [%s] @(%d,%d) scroll=%d", num, cx, cy, scroll_i)
            if badges:
                break
            if xml == prev_xml2 and scroll_i > 0:
                break
            prev_xml2 = xml
            adb(device_addr, "shell", "input", "swipe", "360", "1000", "360", "300", "600")
            time.sleep(1.0)
        # 去重
        seen2 = set()
        unique_badges2 = []
        for b in sorted(badges):
            key = (b[0]//15, b[1]//15)
            if key not in seen2:
                seen2.add(key)
                unique_badges2.append(b)
        badges = unique_badges2

    if not badges:
        lock.release()
        return jsonify({"ok": True, "replied": False, "reason": "no_badge"})

    # 逐个回复所有未读聊天
    import re
    replied_count = 0
    results = []

    while badges:
        # 进聊天
        badge_cy, badge_cx, badge_num = badges.pop(0)
        # 从聊天列表抓绿数字旁边名字（这才是对的）
        chat_name = ""
        for m in re.finditer(r'text=\"([^\"]+)\".*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', xml):
            txt,x1,y1,x2,y2 = m.group(1),*map(int,m.groups()[1:])
            ccy = (y1+y2)//2; ccx = (x1+x2)//2
            if abs(ccy-badge_cy)<70 and ccx<350 and len(txt)>1 and not txt.isdigit():
                chat_name = txt.strip(); break
        adb(device_addr, "shell", "input", "tap", "360", str(badge_cy))
        time.sleep(2)

        # 读消息：兼容不同 LINE 版本，同时查 content-desc 和 text
        d2 = get_u2(device_addr)
        xml_chat = d2.dump_hierarchy()
        last_msg = ""
        # 对方消息特征：左侧气泡（x<屏幕宽度的40%，动态适配）
        for desc, x1, y1, x2, y2 in re.findall(
            r'chat_ui_row_text_message.*?(?:content-desc|text)=\"([^\"]+)\".*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', xml_chat):
            msg = desc.replace("&#10;","").replace("&amp;","&").strip()
            x1_i = int(x1)
            # 放宽到 320px（适配不同分辨率），过滤自己发的消息（右侧气泡）
            if x1_i <= 320 and len(msg) > 1 and len(msg) < 500:
                # 过滤系统消息和广告（放宽条件，只过滤明显的）
                if any(kw in msg for kw in ["專業貸款顧問", "申請流程"]):
                    continue
                last_msg = msg
                break

        if not last_msg:
            logger.info("跳过: chat_name=%s 无对方消息 (xml片段=%s)", chat_name[:20], xml_chat[-200:])
            adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)
            continue  # 跳过这条，处理下一个

        # 生成回复
        is_emoji = len(last_msg.strip()) <= 1
        if is_emoji:
            reply = "你好"
        else:
            try:
                resp = requests.post("https://api.deepseek.com/chat/completions",
                    headers={"Authorization": "Bearer sk-your-deepseek-api-key","Content-Type": "application/json"},
                    json={"model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是環球貸款小助理。用繁體中文回覆2-3句話，語氣親切像朋友聊天。\n\n公司資訊：\n- 申請網址：https://dorrj.com\n- 官方LINE：@583gyplg\n- 首貸最高50000元\n- 全程線上審批30-60分鐘，通過即撥款，不照會\n\n標準方案（8~10天）：\n- 借10000實撥7000\n- 借20000實撥14000\n- 借30000實撥21000\n\n官方LINE審核方案（7天）：\n- 借5000實撥3000 / 借7000實撥4000 / 借10000實撥6000\n- 到期日18:00前還款，遲繳10%罰款，逾期每日15%\n\n客戶有意申請時，引導提供：姓名、電話、身份證字號、居住地址、公司名稱、公司電話、在職時間、每月收入、薪轉勞保、申請金額、資金用途。引導客戶點擊網址 https://dorrj.com 申請，或添加官方LINE @583gyplg。\n\n注意事項：\n- 還款僅轉帳與超商代碼繳費\n- 不主動降價、不承諾一定通過、不提前收費\n- 不要發送身份驗證流程（身分證/自拍影片）除非對方已提交基本資料"},


                            {"role": "user", "content": last_msg}
                        ], "max_tokens": 80}, timeout=30)
                reply = resp.json()["choices"][0]["message"]["content"].strip()
            except:
                reply = ""

        if not reply:
            adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)
            continue

        # 意向判断
        try:
            resp2 = requests.post("https://api.deepseek.com/chat/completions",
                headers={"Authorization": "Bearer sk-your-deepseek-api-key","Content-Type": "application/json"},
                json={"model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "你是貸款意向量表。判斷對方意願1-5級：L1=完全無關(廣告/詐騙/無關話題)，L2=普通聊天/打招呼，L3=對貸款有興趣(詢問方案/利率/條件)，L4=很想貸款(已提供部分資料)，L5=馬上要辦(已提供完整資料/催促撥款)。只回數字如3。"},
                        {"role": "user", "content": last_msg}
                    ], "max_tokens": 5}, timeout=15)
            intent = int(resp2.json()["choices"][0]["message"]["content"].strip())
        except:
            intent = 1
        if intent >= 1:
            # 聊天列表已抓到chat_name，查映射表得LINE ID
            line_id_found = chat_name
            try:
                if os.path.exists("/app/data/contact_map.json"):
                    with open("/app/data/contact_map.json") as f:
                        cm = json.load(f)
                    line_id_found = cm.get(chat_name, chat_name)
            except: pass
            lead = {"level": f"L{intent}", "contact": line_id_found, "msg": last_msg[:100], "time": time.strftime("%Y-%m-%d %H:%M")}
            with open("/app/data/leads.jsonl", "a") as f:
                f.write(json.dumps(lead, ensure_ascii=False) + "\n")
            logger.info("🔥 L%d线索: %s", intent, last_msg[:40])
            # 钉钉推送(替换YOUR_TOKEN)
            try:
                dd_url = "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
                dd_msg = f"🔥 高意向客户 L{intent}\n消息: {last_msg[:60]}\n回复: {reply[:60]}\n时间: {lead['time']}"
                requests.post(dd_url, json={"msgtype": "text", "text": {"content": dd_msg}}, timeout=5)
            except: pass

        # 发送
        inp = d2(description="输入消息")
        if inp.exists(timeout=2):
            inp.click(); time.sleep(0.3); inp.set_text(reply); time.sleep(0.5)
        send_btn = d2(description="发送")
        if send_btn.exists(timeout=3):
            send_btn.click(); time.sleep(1)
        else:
            d2.press("enter"); time.sleep(1)

        replied_count += 1
        results.append({"msg": last_msg[:30], "reply": reply[:30]})
        logger.info("[%d] %s → %s", replied_count, last_msg[:20], reply[:20])

        # 回聊天列表
        adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)

        # 重扫剩余绿数字（也要滚屏查找）
        for scroll_i in range(MAX_SCROLLS):
            d3 = get_u2(device_addr)
            xml2 = d3.dump_hierarchy()
            badges = []
            for m in re.finditer(r'text=\"(\d+)\".*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', xml2):
                num, x1, y1, x2, y2 = m.group(1), *map(int, m.groups()[1:])
                cx, cy = (x1+x2)//2, (y1+y2)//2
                if cx > 500 and 400 < cy < 1050 and 1 <= int(num) <= 999:
                    badges.append((cy, cx, int(num)))
            badges.sort()
            if badges:
                break
            # 没找到就继续上滑
            adb(device_addr, "shell", "input", "swipe", "360", "1000", "360", "300", "600")
            time.sleep(0.8)

    # 全部回完，回聊天列表方便下次巡逻
    adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(1)

    lock.release()
    return jsonify({"ok": True, "replied": replied_count > 0, "count": replied_count, "results": results})


@app.route("/line/send-message", methods=["POST"])
def line_send_message():
    """给指定对话发消息"""
    data = request.get_json()
    chat_name = data.get("chat_name", "")
    text = data["text"]
    device_addr = _resolve_device()

    ensure_connected(device_addr)

    adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(2)

    if chat_name:
        adb(device_addr, "shell", "input", "tap", "650", "80")
        time.sleep(1)
        adb(device_addr, "shell", "input", "text", chat_name.replace(" ", "%s"))
        time.sleep(1)
        adb(device_addr, "shell", "input", "keyevent", "KEYCODE_ENTER")
        time.sleep(2)
        adb(device_addr, "shell", "input", "tap", "360", "280")
        time.sleep(1.5)

    adb(device_addr, "shell", "input", "tap", "360", "1200")
    time.sleep(0.5)
    adb(device_addr, "shell", "input", "text", text.replace(" ", "%s"))
    time.sleep(0.5)
    adb(device_addr, "shell", "input", "keyevent", "KEYCODE_ENTER")
    time.sleep(0.5)

    return jsonify({"ok": True, "text": text, "chat_name": chat_name, "device": device_addr})


@app.route("/line/check-inbox", methods=["POST"])
def check_inbox():
    """检查收件箱 — 只返回未读聊天"""
    device_addr = _resolve_device()
    ensure_connected(device_addr)

    # 强制重启 LINE，确保在干净状态
    adb(device_addr, "shell", "am", "force-stop", LINE_PACKAGE)
    time.sleep(1)
    adb(device_addr, "shell", "monkey", "-p", LINE_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    # 切到聊天 Tab
    adb(device_addr, "shell", "input", "tap", "216", "1128")
    time.sleep(1)

    adb(device_addr, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    time.sleep(0.5)
    r = adb_raw(device_addr, "exec-out", "cat", "/sdcard/ui.xml")
    ui_xml = r["stdout_bytes"].decode("utf-8", errors="replace") if r["ok"] else ""

    if not ui_xml:
        return jsonify({"ok": False, "error": "无法读取UI"}), 500

    chat_rows = re.findall(
        r'<node[^>]*content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*>',
        ui_xml
    )

    data = request.get_json() or {}
    known_chats = data.get("known_chats", [])
    messages = []

    skip_keywords = ["LINE", "公告", "AD", "官方", "貼圖", "Keep", "今日", "您可能", "未来", "聊天选项",
                     "关闭", "新消息", "按钮"]

    for desc, x1, y1, x2, y2 in chat_rows:
        if not desc.strip():
            continue
        if any(kw in desc for kw in skip_keywords):
            continue

        desc_clean = desc.strip()
        is_new = True
        for known in known_chats:
            if known in desc_clean[:30]:
                is_new = False
                break

        if is_new or not known_chats:
            cx = (int(x1) + int(x2)) // 2
            cy = (int(y1) + int(y2)) // 2
            messages.append({
                "sender_hint": desc_clean[:60],
                "center": [cx, cy]
            })

    detailed_messages = []
    for msg in messages[:5]:
        cx, cy = msg["center"]
        adb(device_addr, "shell", "input", "tap", str(cx), str(cy))
        time.sleep(2)

        adb(device_addr, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
        time.sleep(0.5)
        r2 = adb_raw(device_addr, "exec-out", "cat", "/sdcard/ui.xml")
        chat_xml = r2["stdout_bytes"].decode("utf-8", errors="replace") if r2["ok"] else ""

        if chat_xml:
            chat_texts = re.findall(r'text="([^"]+)"', chat_xml)
            msg_elements = re.findall(
                r'chat_ui_row_text_message[^>]*content-desc="([^"]*)"', chat_xml
            )
            detailed_messages.append({
                "sender_hint": msg["sender_hint"],
                "raw_texts": chat_texts[-10:] if len(chat_texts) > 10 else chat_texts,
                "message_previews": msg_elements[-3:] if len(msg_elements) > 3 else msg_elements
            })

        adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.5)

    return jsonify({
        "ok": True,
        "device": device_addr,
        "messages": detailed_messages,
        "count": len(detailed_messages)
    })


if __name__ == "__main__":
    # 启动时连接所有已注册设备
    for dev_id, info in DEVICES.items():
        addr = info["addr"]
        logger.info("连接设备 %s: %s ...", dev_id, addr)
        subprocess.run([ADB_CMD, "connect", addr], capture_output=True)
        time.sleep(1)

    logger.info("设备注册表: %s", json.dumps(DEVICES, ensure_ascii=False))
    logger.info("ADB Bridge v2 (多设备) 启动在 :8899")
    from waitress import serve
    serve(app, host="0.0.0.0", port=8899, threads=8)
