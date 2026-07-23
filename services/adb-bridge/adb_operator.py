"""
AdbOperator — ADB 层，只负责操作手机。

职责：
  - ADB 命令执行
  - uiautomator2 元素查找/点击/输入
  - DeepSeek AI 屏幕识别
  - 设备连接管理

禁止：
  - 直接写数据库（不 import db.py / AccountManager）
  - 调用 Scheduler
  - 调用 Flask

所有方法返回标准 dict，由业务层调用 AccountManager.report() 入库。
"""
import subprocess, json, time, re, base64, logging, requests, os
import uiautomator2 as u2
import threading

logger = logging.getLogger(__name__)


class AdbOperator:
    """ADB 操作层 — 纯手机操作，不碰数据库"""

    ADB_CMD = "/usr/local/bin/adb"
    ADB_TIMEOUT = 15
    LINE_PACKAGE = "jp.naver.line.android"
    DEVICES_FILE = "/app/data/devices.json"

    def __init__(self, devices: dict = None):
        """
        devices: {device_id: {addr, type, label}}
        如果不传，从 DEVICES_FILE 加载。
        """
        self.DEVICES = devices or self._load_devices()
        self._u2_cache: dict[str, object] = {}
        self._device_locks: dict[str, threading.Lock] = {}
        self._deepseek_session = None
        self._deepseek_lock = threading.Lock()

    # ─── 设备注册 ───

    def _load_devices(self) -> dict:
        devs = {
            "cloud-01": {"addr": "your-cloud-phone-ip:499", "type": "cloud", "label": "云手机-OPPO"}
        }
        if os.path.exists(self.DEVICES_FILE):
            try:
                with open(self.DEVICES_FILE) as f:
                    saved = json.load(f)
                    for dev_id, info in saved.items():
                        devs[dev_id] = info
                logger.info("从文件加载设备: %s", list(saved.keys()))
            except Exception:
                pass
        return devs

    def reload_devices(self):
        """重新从文件加载设备（热更新）"""
        self.DEVICES = self._load_devices()

    def get_device_addr(self, device_id: str = None) -> str:
        """解析 device_id → ADB 地址"""
        if device_id and device_id in self.DEVICES:
            return self.DEVICES[device_id]["addr"]
        if device_id and ":" in device_id:
            return device_id
        first = next(iter(self.DEVICES.values()))
        return first["addr"]

    def get_device_lock(self, addr: str) -> threading.Lock:
        if addr not in self._device_locks:
            self._device_locks[addr] = threading.Lock()
        return self._device_locks[addr]

    # ─── uiautomator2 ───

    def get_u2(self, device_addr: str):
        """获取 uiautomator2 连接（带缓存，自动重连）"""
        if device_addr in self._u2_cache:
            try:
                self._u2_cache[device_addr].info
                return self._u2_cache[device_addr]
            except Exception:
                pass
        self._u2_cache[device_addr] = u2.connect(device_addr)
        return self._u2_cache[device_addr]

    def clear_u2_cache(self, device_addr: str = None):
        """清除 u2 缓存（页面跳转后调用）"""
        if device_addr:
            self._u2_cache.pop(device_addr, None)
        else:
            self._u2_cache.clear()

    # ─── ADB 命令 ───

    def adb(self, device_addr: str, *args, timeout=None):
        """执行 ADB 命令"""
        cmd = [self.ADB_CMD, "-s", device_addr] + list(args)
        logger.info("ADB[%s]: %s", device_addr, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=timeout or self.ADB_TIMEOUT)
            return {"ok": result.returncode == 0,
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": "timeout"}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}

    def adb_raw(self, device_addr: str, *args, timeout=None):
        """执行 ADB 命令（二进制输出）"""
        cmd = [self.ADB_CMD, "-s", device_addr] + list(args)
        logger.info("ADB_RAW[%s]: %s", device_addr, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=timeout or self.ADB_TIMEOUT)
            return {"ok": result.returncode == 0, "stdout_bytes": result.stdout,
                    "stderr": result.stderr.decode(errors="replace").strip()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout_bytes": b"", "stderr": "timeout"}
        except Exception as e:
            return {"ok": False, "stdout_bytes": b"", "stderr": str(e)}

    def ensure_connected(self, device_addr: str, max_retries: int = 3) -> bool:
        """确保 ADB 连接，指数退避重连"""
        r = self.adb(device_addr, "shell", "echo", "ok", timeout=5)
        if r["ok"] and "ok" in r["stdout"]:
            return True
        for attempt in range(1, max_retries + 1):
            wait_s = 2 ** attempt
            logger.warning("ADB 断开 [%s]，第 %d/%d 次重连 (等待 %ds)...",
                           device_addr, attempt, max_retries, wait_s)
            subprocess.run([self.ADB_CMD, "connect", device_addr],
                          capture_output=True, timeout=10)
            time.sleep(wait_s)
            r = self.adb(device_addr, "shell", "echo", "ok", timeout=5)
            if r["ok"] and "ok" in r["stdout"]:
                logger.info("ADB 重连成功 [%s] (第%d次)", device_addr, attempt)
                return True
        logger.error("ADB 重连失败 [%s] (已重试%d次)", device_addr, max_retries)
        return False

    # ─── DeepSeek AI ───

    def _get_deepseek_session(self) -> requests.Session:
        if self._deepseek_session is None:
            with self._deepseek_lock:
                if self._deepseek_session is None:
                    self._deepseek_session = requests.Session()
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=3, pool_maxsize=10, max_retries=0)
                    self._deepseek_session.mount("https://", adapter)
                    logger.info("DeepSeek 连接池已初始化 (pool=3/10)")
        return self._deepseek_session

    def deepseek_chat(self, messages: list, max_tokens: int = 80,
                      timeout: int = 30, max_retries: int = 3) -> str:
        """DeepSeek API 调用，连接池 + 指数退避"""
        session = self._get_deepseek_session()
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"model": "deepseek-chat", "messages": messages,
                          "max_tokens": max_tokens},
                    timeout=timeout)
                if resp.status_code == 429:
                    wait_s = 2 ** attempt
                    logger.warning("DeepSeek 限流(429)，%ds 后退避 (第%d/%d次)",
                                   wait_s, attempt, max_retries)
                    time.sleep(wait_s)
                    continue
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except requests.exceptions.Timeout:
                last_error = "timeout"
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
            except Exception as e:
                last_error = str(e)[:100]
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
        raise Exception(f"DeepSeek API 重试耗尽 (last_error={last_error})")

    # ─── UI 操作 ───

    def ui_find(self, device_addr: str, text_contains: str, timeout=3):
        """uiautomator dump → 查找包含指定文字的元素坐标"""
        for _ in range(timeout):
            subprocess.run(
                [self.ADB_CMD, "-s", device_addr, "shell", "uiautomator", "dump",
                 "/sdcard/ui.xml"],
                capture_output=True, timeout=10)
            r = self.adb_raw(device_addr, "exec-out", "cat", "/sdcard/ui.xml")
            if not r["ok"]:
                time.sleep(1)
                continue
            xml = r["stdout_bytes"].decode("utf-8", errors="replace")
            # 优先 clickable
            m = re.search(
                rf'text="[^"]*{re.escape(text_contains)}[^"]*"[^>]*clickable="true"[^>]*'
                rf'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
            if m:
                cx = (int(m.group(1)) + int(m.group(3))) // 2
                cy = (int(m.group(2)) + int(m.group(4))) // 2
                logger.info("UI_FIND[%s] [%s] → (%d, %d)", device_addr, text_contains, cx, cy)
                return (cx, cy)
            # 退一步：不要求 clickable
            m = re.search(
                rf'text="[^"]*{re.escape(text_contains)}[^"]*"[^>]*'
                rf'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
            if m:
                cx = (int(m.group(1)) + int(m.group(3))) // 2
                cy = (int(m.group(2)) + int(m.group(4))) // 2
                logger.info("UI_FIND(any)[%s] [%s] → (%d, %d)", device_addr, text_contains, cx, cy)
                return (cx, cy)
            time.sleep(1)
        return None

    def ui_tap(self, device_addr: str, text: str, timeout=3) -> bool:
        """找到包含 text 的元素并点击"""
        pos = self.ui_find(device_addr, text, timeout)
        if pos:
            self.adb(device_addr, "shell", "input", "tap", str(pos[0]), str(pos[1]))
            return True
        logger.warning("UI_TAP[%s] [%s] not found", device_addr, text)
        return False

    def type_text(self, device_addr: str, text: str):
        """通过 uiautomator2 输入文字（支持中文/emoji）"""
        d = self.get_u2(device_addr)
        # LINE 聊天输入框
        el = d(resourceId="jp.naver.line.android:id/chat_ui_message_edit")
        if el.exists:
            el.click()
            time.sleep(0.3)
            el.set_text(text)
            logger.info("TYPED[%s](chat): %s", device_addr, text[:50])
            return
        # 已聚焦的编辑框
        el = d(focused=True, className="android.widget.EditText")
        if el.exists:
            el.set_text(text)
            logger.info("TYPED[%s](search): %s", device_addr, text[:50])
            return
        # 任意 EditText
        el = d(className="android.widget.EditText")
        if el.exists:
            el.click()
            time.sleep(0.3)
            el.set_text(text)
            logger.info("TYPED[%s](edit): %s", device_addr, text[:50])
            return
        d.send_keys(text)
        logger.info("TYPED[%s](fallback): %s", device_addr, text[:50])

    # ─── 简单手机操作 ───

    def open_line(self, device_addr: str) -> bool:
        """打开 LINE"""
        self.adb(device_addr, "shell", "am", "force-stop", self.LINE_PACKAGE)
        time.sleep(1)
        self.adb(device_addr, "shell", "monkey", "-p", self.LINE_PACKAGE,
                  "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(4)
        d = self.get_u2(device_addr)
        if d(text="未读消息").exists(timeout=1) or d(text="接收时间").exists(timeout=1):
            self.adb(device_addr, "shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)
        return True

    def close_line(self, device_addr: str) -> bool:
        """关闭 LINE"""
        self.adb(device_addr, "shell", "am", "force-stop", self.LINE_PACKAGE)
        return True

    def screenshot(self, device_addr: str) -> dict:
        """截图返回 base64"""
        r = self.adb_raw(device_addr, "exec-out", "screencap", "-p")
        if r["ok"]:
            return {"ok": True, "image_base64": base64.b64encode(r["stdout_bytes"]).decode()}
        return {"ok": False, "error": r["stderr"]}

    def tap(self, device_addr: str, x: int, y: int) -> dict:
        """点击坐标"""
        r = self.adb(device_addr, "shell", "input", "tap", str(x), str(y))
        return {"ok": r["ok"]}

    def swipe(self, device_addr: str, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = 500) -> dict:
        """滑动"""
        r = self.adb(device_addr, "shell", "input", "swipe",
                      str(x1), str(y1), str(x2), str(y2), str(duration_ms))
        return {"ok": r["ok"]}

    def send_keyevent(self, device_addr: str, keycode: str) -> dict:
        """发送按键"""
        r = self.adb(device_addr, "shell", "input", "keyevent", keycode)
        return {"ok": r["ok"]}

    def get_device_info(self, device_addr: str) -> dict:
        """获取设备信息"""
        try:
            d = self.get_u2(device_addr)
            info = d.info
            return {
                "ok": True,
                "model": info.get("productName", "unknown"),
                "android": str(info.get("sdkInt", "?")),
                "resolution": f"{info.get('displayWidth',0)}x{info.get('displayHeight',0)}",
                "battery": str(info.get("battery", {}).get("level", "?")) if isinstance(info.get("battery"), dict) else "?",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ─── 高级流程：加好友 ───

    def add_friend_by_id(self, device_addr: str, line_id: str,
                         message: str = "") -> dict:
        """
        通过 LINE ID 添加好友。
        返回标准 result dict（不含 DB 操作，由调用方 report）。
        """
        t_start = time.time()
        steps = []

        # 1. 重启 LINE → 点主页
        self.adb(device_addr, "shell", "am", "force-stop", self.LINE_PACKAGE)
        time.sleep(2)
        self.adb(device_addr, "shell", "monkey", "-p", self.LINE_PACKAGE,
                  "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(6)
        u2home = self.get_u2(device_addr)
        home_el = u2home(text="主页")
        if home_el.exists(timeout=2):
            b = home_el.info['bounds']
            self.adb(device_addr, "shell", "input", "tap",
                      str((b['left']+b['right'])//2), str((b['top']+b['bottom'])//2))
        else:
            self.adb(device_addr, "shell", "input", "tap", "72", "1128")
        time.sleep(4)
        steps.append("goto_home")

        # 2. 点➕
        self.adb(device_addr, "shell", "input", "tap", "588", "102")
        time.sleep(4)
        steps.append("tap_add_friend_btn")

        # 2b. 验证到了添加好友页
        u2check = self.get_u2(device_addr)
        if not u2check(description="搜索").exists(timeout=3) and not u2check(text="搜索").exists(timeout=2):
            logger.info("点➕后页面不对，重启")
            steps.append("bad_page")
            self.adb(device_addr, "shell", "am", "force-stop", self.LINE_PACKAGE)
            time.sleep(2)
            self.adb(device_addr, "shell", "monkey", "-p", self.LINE_PACKAGE,
                      "-c", "android.intent.category.LAUNCHER", "1")
            time.sleep(5)
            self.adb(device_addr, "shell", "input", "tap", "72", "1128"); time.sleep(2)
            u2r = self.get_u2(device_addr)
            ab = u2r(description="添加好友")
            if ab.exists(timeout=2): ab.click()
            else: self.adb(device_addr, "shell", "input", "tap", "588", "102")
            time.sleep(3)
            steps.append("retry_plus")

        # 3. 放大镜搜索
        self.adb(device_addr, "shell", "input", "tap", "600", "271")
        time.sleep(3)
        steps.append("tap_search_icon")

        # 4. 验证ID标签
        xd3 = self.get_u2(device_addr)
        if not xd3(text="ID").exists(timeout=2):
            logger.info("找不到ID标签，页面错误，重启重试")
            steps.append("page_error")
            self.adb(device_addr, "shell", "am", "force-stop", self.LINE_PACKAGE)
            time.sleep(2)
            self.adb(device_addr, "shell", "monkey", "-p", self.LINE_PACKAGE,
                      "-c", "android.intent.category.LAUNCHER", "1")
            time.sleep(5)
            self.adb(device_addr, "shell", "input", "tap", "72", "1128"); time.sleep(2)
            self.adb(device_addr, "shell", "input", "tap", "588", "102"); time.sleep(3)
            self.adb(device_addr, "shell", "input", "tap", "600", "271"); time.sleep(3)
            steps.append("retry_nav")

        self.adb(device_addr, "shell", "input", "tap", "78", "248")
        time.sleep(0.5)
        steps.append("tab_id")

        # 5. 输入 LINE ID
        self.adb(device_addr, "shell", "input", "tap", "336", "356")
        time.sleep(0.3)
        for _ in range(5):
            self.adb(device_addr, "shell", "input", "keyevent", "KEYCODE_DEL")
            time.sleep(0.05)
        self.type_text(device_addr, line_id)
        time.sleep(0.5)
        steps.append("type_id")

        # 6. 点搜索
        self.adb(device_addr, "shell", "input", "tap", "650", "356")
        time.sleep(3)
        steps.append("search")

        # 7. 检查结果
        xd = self.get_u2(device_addr)
        xd_xml = xd.dump_hierarchy()
        if "未找到" in xd_xml:
            steps.append("no_result")
            return {"ok": False, "task_type": "add_friend",
                    "steps": steps, "line_id": line_id,
                    "last_step": "no_result", "error": "未找到该用户",
                    "search_count": 1}

        # 搜索上限检测
        for kw in ["已达上限", "搜索次数", "过于频繁", "稍后再试", "限制"]:
            if kw in xd_xml:
                steps.append("search_limit")
                return {"ok": False, "task_type": "add_friend",
                        "steps": steps, "line_id": line_id,
                        "last_step": "search_limit", "error": "search_limit",
                        "search_count": 1}

        # 点「添加」
        if not self.ui_tap(device_addr, "添加", timeout=2):
            self.adb(device_addr, "shell", "input", "tap", "360", "834")
            time.sleep(1)
            steps.append("tap_add_xy")
        else:
            steps.append("tap_add")
        time.sleep(1.5)

        # 8. 二次确认
        self.ui_tap(device_addr, "添加", timeout=1)
        time.sleep(0.5)
        steps.append("confirm_add")

        # 保存 contact_map（文件操作，不算 DB）
        try:
            cj = {}
            if os.path.exists("/app/data/contact_map.json"):
                with open("/app/data/contact_map.json") as f:
                    cj = json.load(f)
            cj[line_id] = line_id
            os.makedirs("/app/data", exist_ok=True)
            with open("/app/data/contact_map.json", "w") as f:
                json.dump(cj, f, ensure_ascii=False)
        except Exception:
            pass

        # 9. 点「聊天」→ 发招呼 → 改名
        time.sleep(2)
        d = self.get_u2(device_addr)
        chat_btn = d(text="聊天")
        if not chat_btn.exists(timeout=1):
            chat_btn = d(description="聊天")
        if not chat_btn.exists(timeout=1):
            chat_btn = d(descriptionContains="聊天")
        if chat_btn.exists(timeout=2):
            chat_btn.click()
            time.sleep(2)
            inp = d(description="输入消息")
            if inp.exists(timeout=2):
                inp.click()
                time.sleep(0.3)
                if not message:
                    message = "你好，我是貸款顧問"
                self.type_text(device_addr, message)
                time.sleep(0.5)
                send_btn = d(description="发送")
                if send_btn.exists(timeout=3):
                    send_btn.click()
                    time.sleep(1)
                    steps.append("greeted")

                    # 改名
                    renamed_ok = False
                    time.sleep(2)
                    for el in d(clickable=True):
                        try:
                            b = el.info.get("bounds", {})
                            w = b.get("right", 0) - b.get("left", 0)
                            cy = (b.get("top", 0) + b.get("bottom", 0)) // 2
                            if cy < 150 and w > 200:
                                nx = b["left"] + int(w * 0.7)
                                self.adb(device_addr, "shell", "input", "tap", str(nx), str(cy))
                                time.sleep(2)
                                break
                        except Exception:
                            pass
                    self.clear_u2_cache(device_addr)
                    xd3 = u2.connect(device_addr)
                    xd_xml = xd3.dump_hierarchy()
                    if "修改名字" in xd_xml:
                        m = re.search(
                            r'(?:text|content-desc)="修改名字"[^>]*'
                            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xd_xml)
                        if m:
                            cx = (int(m.group(1)) + int(m.group(3))) // 2
                            cy = (int(m.group(2)) + int(m.group(4))) // 2
                            self.adb(device_addr, "shell", "input", "tap", str(cx), str(cy))
                            time.sleep(1)
                            self.type_text(device_addr, line_id)
                            time.sleep(0.5)
                            self.adb(device_addr, "shell", "input", "tap", "360", "1122")
                            time.sleep(1)
                            renamed_ok = True
                            steps.append("renamed")
                    if not renamed_ok:
                        steps.append("rename_fail")
                else:
                    steps.append("send_fail")
            else:
                steps.append("chat_page_fail")
        else:
            steps.append("no_chat_yet")

        dt_ms = (time.time() - t_start) * 1000
        return {
            "ok": True,
            "task_type": "add_friend",
            "steps": steps,
            "line_id": line_id,
            "last_step": steps[-1] if steps else "none",
            "search_count": 1,
            "duration_ms": dt_ms,
        }
