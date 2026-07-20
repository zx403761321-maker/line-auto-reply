"""
LINE 应用 UI 操作
封装所有 LINE APP 内的交互：搜索好友、进入聊天、读取消息、发送消息
"""
import re
import time
import random
import logging
import uiautomator2 as u2
from .adb_helper import (
    tap, swipe, input_text_adbkeyboard, start_app, press_key, adb_command
)

log = logging.getLogger(__name__)

LINE_PACKAGE = "jp.naver.line.android"

# ─── UI 常量（随 LINE 版本可能变化，用坐标+文本双重定位） ───
COORDS = {
    "chat_tab": (216, 1151),    # 聊天标签页（底部）
    "search_box": (117, 194),   # 搜索框（顶部放大镜）
    "send_btn_area": (325, 1080),  # 输入框区域（点击唤起键盘）
}

GREETINGS = [
    "早安呀，今天天氣看起來不錯呢～ 不知道您早上習慣喝杯咖啡還是茶？新的一天順順利利",
    "早呀，剛看到窗外陽光挺好的，想起台灣這時候的早晨總是很舒服～ 希望您今天也有好心情，一切順利！",
    "早安，新的一天開始啦～ 不管今天要忙什麼，記得稍微偷個懶喘口氣哦，祝您工作順利，心情開朗！",
    "早呀，聽說今天適合喝杯溫溫的珍珠奶茶開啟一天呢～ 希望您今天也能被小美好包圍，順順利利的！",
    "您好呀，早安～ 今天天氣看起來不錯呢，不知道您那邊陽光夠不夠暖？新的一天開始，先祝您開開心心，事事順利，有什麼需要搭把手的，隨時喊我哦～",
]


def goto_home(device_addr: str):
    """回到 LINE 聊天列表主页"""
    start_app(device_addr, LINE_PACKAGE)
    time.sleep(3)
    tap(device_addr, *COORDS["chat_tab"])
    time.sleep(1)


def search_and_enter_chat(device_addr: str, line_id: str) -> bool:
    """
    搜索好友 LINE ID 并进入聊天。

    流程：
    1. 点击搜索框
    2. 输入 LINE ID
    3. 按回车搜索
    4. 判断是直接进聊天还是搜索结果页
    5. 如果是搜索结果页，点击「聊天」按钮

    Returns:
        True 如果成功进入聊天
    """
    d = u2.connect(device_addr)

    # 点搜索框
    tap(device_addr, *COORDS["search_box"])
    time.sleep(2)

    # 输入 LINE ID
    input_text_adbkeyboard(device_addr, line_id)
    time.sleep(0.5)
    press_key(device_addr, "KEYCODE_ENTER")
    time.sleep(4)

    # 验证：是否在聊天页
    xml = d.dump_hierarchy()

    # 情况1: 搜索直接跳进聊天页（已有聊天记录的好友）
    if "發給" in xml or "传送" in xml or "傳送" in xml:
        return True

    # 情况2: 搜索结果页 → 找「聊天」按钮
    if "聊天" in xml or "Chat" in xml:
        # 尝试用 text 定位
        chat_btn = d(text="聊天")
        if not chat_btn.exists(timeout=1):
            chat_btn = d(textContains="聊天")
        if chat_btn.exists(timeout=1):
            chat_btn.click()
            time.sleep(3)
            return True

    return False


def read_chat_messages(device_addr: str, my_replies: set) -> list:
    """
    读取当前聊天窗口中对方发来的消息。

    Args:
        device_addr: ADB 设备地址
        my_replies: 自己发过的消息集合（用于过滤）

    Returns:
        对方消息列表（按时间排序，最新在最后）
    """
    d = u2.connect(device_addr)
    xml = d.dump_hierarchy()

    # 匹配消息格式
    rows = re.findall(
        r'chat_ui_row_text_message.*?content-desc="([^"]*)".*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml
    )

    other_msgs = []
    for desc, x1, y1, x2, y2 in rows:
        msg = desc.replace("&#10;", "").replace("&amp;", "&").strip()
        if len(msg) < 1 or len(msg) > 300:
            continue
        if msg in my_replies:  # 过滤自己发的
            continue
        if int(x1) <= 250:     # 左排 = 对方消息
            other_msgs.append(msg)

    return other_msgs


def send_message(device_addr: str, text: str) -> bool:
    """
    发送消息到当前聊天窗口。

    流程：
    1. 点击输入框
    2. 用 uiautomator2 set_text 输入
    3. 点击发送按钮

    Returns:
        True 如果成功发送
    """
    d = u2.connect(device_addr)

    # 找到输入框
    el = d(resourceId="jp.naver.line.android:id/chat_ui_message_edit")
    if not el.exists(timeout=2):
        # 回退：点输入区域唤醒键盘
        d.click(*COORDS["send_btn_area"])
        time.sleep(0.5)
        el = d(resourceId="jp.naver.line.android:id/chat_ui_message_edit")

    if not el.exists(timeout=2):
        log.error("找不到输入框")
        return False

    el.click()
    time.sleep(0.3)
    el.set_text(text)
    time.sleep(0.5)

    # 点发送
    send_btn = d(description="发送")
    if not send_btn.exists(timeout=1):
        send_btn = d(descriptionContains="送")
    if send_btn.exists(timeout=1):
        send_btn.click()
    else:
        # 回退：按回车
        press_key(device_addr, "KEYCODE_ENTER")

    time.sleep(1)
    return True


def check_if_friend_exists(device_addr: str, line_id: str) -> bool:
    """
    检查某个 LINE ID 是否已经是好友。
    通过搜索该 ID，看是否能直接进入聊天页。

    Returns:
        True 如果是好友
    """
    goto_home(device_addr)
    return search_and_enter_chat(device_addr, line_id)


def send_greeting(device_addr: str, line_id: str):
    """给新好友发首条问候"""
    msg = random.choice(GREETINGS)
    log.info(f"  发送首条问候 → {line_id}: {msg[:50]}...")
    return send_message(device_addr, msg)
