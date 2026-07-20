#!/usr/bin/env python3
"""
账号互聊 — 模拟真人对话，每天随机聊几回
"""
import requests, time, random, json, os
from datetime import datetime

BRIDGE = "http://127.0.0.1:8899"

# 账号配对（日后加号只加一列）
PAIRS = [
    {"from": "cloud-03", "to": "cloud-05", "chat_name": "56324566"},
    {"from": "cloud-05", "to": "cloud-03", "chat_name": "0980363769"},
]

# 闲聊话题池（日常对话）
CHATS = [
    "今天天氣還不錯欸，你有出門嗎？",
    "最近忙什麼呢～好久沒聊了",
    "午安呀，吃飯了嗎？",
    "今天工作好累喔，你呢？",
    "週末有沒有去哪裡玩呀？",
    "剛看到一個好笑的影片，改天分享給你",
    "最近有沒有推薦的餐廳啊？",
    "這幾天一直下雨，好煩喔",
    "早安～今天也要加油喔！",
    "晚安啦，早點休息～",
    "最近股票你有在看嗎？",
    "中午吃什麼好呢，給點建議吧",
    "今天好熱，冷氣開整天了 😅",
    "剛在路上看到一隻超可愛的貓",
    "你有沒有看最近那部新電影？",
    "放假有沒有計畫去哪裡？",
    "今天請假在家休息，好爽",
    "剛剛喝了杯珍珠奶茶，好滿足",
    "最近在追什麼劇嗎？",
    "這禮拜好快就過了欸",
]

# 回复池（对方回的消息）
REPLIES = [
    "對啊，今天天氣真的很好～",
    "還好啦，都在忙工作的事",
    "吃了！你呢？",
    "辛苦了，下班好好休息",
    "還沒欸，可能就在家吧",
    "哈哈哈快分享給我",
    "有啊，上次去的那間不錯",
    "真的，下到快發霉了",
    "早安！你也是～",
    "晚安～你也早點睡",
    "有在關注幾支，但最近不太穩",
    "我覺得吃便當就好啦",
    "哈哈我也是，冷氣費好貴",
    "好可愛喔！！有拍照嗎",
    "還沒看欸，好看嗎？",
    "想去花蓮，還在規劃中",
    "好好喔，羨慕！",
    "珍珠奶茶真的會上癮 😂",
    "有啊，最近在看一部韓劇",
    "對啊，時間過好快",
]

LOG = "/root/line-crm/logs/inter_chat.log"


def send_msg(device, chat_name, text):
    """通过 API 发消息"""
    try:
        resp = requests.post(
            f"{BRIDGE}/line/send-message?device={device}",
            json={"chat_name": chat_name, "text": text},
            timeout=60
        )
        return resp.json().get("ok", False)
    except Exception as e:
        return False


def do_chat(device, chat_name, topic, reply):
    """一次对话：发起话题 → 等待 → 自己接着回"""
    t = datetime.now().strftime("%H:%M")
    log_line = f"[{t}] {device} → {chat_name}"

    if send_msg(device, chat_name, topic):
        log_line += f" 话题:{topic[:15]}..."
        # 间隔1-30分钟再发一句（模拟断断续续聊天）
        delay = random.randint(60, 1800)
        time.sleep(delay)
        if send_msg(device, chat_name, reply):
            log_line += f" ✅ 追加:{reply[:15]}..."
        else:
            log_line += " ⚠️追发失败"
    else:
        log_line += " ❌话题失败"

    with open(LOG, "a") as f:
        f.write(log_line + "\n")
    print(log_line)


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    print("互聊脚本启动，随机间隔2-6小时聊一次")

    while True:
        now = datetime.now()
        hour = now.hour

        # 只在 8:00 - 23:00 之间聊
        if 8 <= hour < 23:
            # 随机选一对（日后扩展多对）
            pair = random.choice(PAIRS)
            topic = random.choice(CHATS)
            reply = random.choice(REPLIES)

            do_chat(pair["from"], pair["chat_name"], topic, reply)

        # 随机 2-6 小时后再聊
        delay = random.randint(7200, 21600)
        next_time = datetime.now().strftime("%H:%M")
        print(f"  下次聊天大约在 {next_time} (间隔 {delay//60} 分钟)")
        time.sleep(delay)


if __name__ == "__main__":
    main()
