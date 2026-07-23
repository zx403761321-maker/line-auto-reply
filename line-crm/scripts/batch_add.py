#!/usr/bin/env python3
"""
批量添加 LINE 好友
用法:
  python3 batch_add.py targets.txt                    # 添加全部
  python3 batch_add.py targets.txt --delay 90 180     # 间隔90-180秒
  python3 batch_add.py targets.txt --dry-run           # 预览不执行
  python3 batch_add.py targets.txt --resume            # 从上次中断处继续
  python3 batch_add.py targets.txt --start 5           # 从第5个开始
"""
import requests, time, random, sys, os, json, argparse
from datetime import datetime

BRIDGE = "http://127.0.0.1:8899"
RESULT_FILE = "/root/line-crm/data/state/batch_result.json"
DEFAULT_DEVICE = "cloud-01"

# ─── 首条问候语池（网址文案，随机轮换）───
GREETINGS = [
    "資金周轉不求人，線上快速申請 👉 https://dorrj.com",
    "急需用錢？最快1小時撥款，點這申請 👉 https://dorrj.com",
    "臨時缺資金？線上審核不照會，馬上申請 👉 https://dorrj.com",
    "小額周轉無壓力，線上快速到帳 👉 https://dorrj.com",
    "資金卡關？線上申請30分鐘審核通過 👉 https://dorrj.com",
    "不用出門，手機就能申請借款 👉 https://dorrj.com",
    "短期周轉救急，線上審批當天撥款 👉 https://dorrj.com",
    "缺錢周轉？點我快速申請，免照會 👉 https://dorrj.com",
]


def load_targets(filepath):
    """读取目标列表（一行一个LINE ID）"""
    targets = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    return targets


def load_results():
    """读取已执行结果"""
    if os.path.exists(RESULT_FILE):
        with open(RESULT_FILE) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "pending": []}


def save_results(results):
    """保存结果"""
    os.makedirs(os.path.dirname(RESULT_FILE), exist_ok=True)
    with open(RESULT_FILE, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def add_one(line_id, greeting, device):
    """添加一个好友，返回 (success, detail)"""
    try:
        resp = requests.post(
            f"{BRIDGE}/line/add-friend-by-id?device={device}",
            json={"line_id": line_id, "message": greeting},
            timeout=90
        )
        data = resp.json()
        ok = data.get("ok", False)
        steps = data.get("steps", [])
        detail = " → ".join(steps)
        return ok, detail
    except requests.exceptions.Timeout:
        return False, "请求超时"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="批量添加 LINE 好友")
    parser.add_argument("targets", help="目标列表文件（一行一个LINE ID）")
    parser.add_argument("--delay-min", type=int, default=90, help="最小间隔秒数 (默认90)")
    parser.add_argument("--delay-max", type=int, default=180, help="最大间隔秒数 (默认180)")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际添加")
    parser.add_argument("--resume", action="store_true", help="从上次中断处继续")
    parser.add_argument("--start", type=int, default=0, help="从第N个开始 (0-based)")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="设备ID")
    args = parser.parse_args()

    device = args.device

    # 加载
    targets = load_targets(args.targets)
    results = load_results()
    completed_ids = {r["line_id"] for r in results["completed"]}
    failed_ids = {r["line_id"] for r in results["failed"]}

    # 过滤已完成的
    if args.resume:
        pending = [t for t in targets if t not in completed_ids and t not in failed_ids]
    else:
        pending = targets[args.start:]

    if not pending:
        print("✅ 所有目标已完成！")
        return

    total = len(targets)
    done = total - len(pending)
    print(f"=" * 60)
    print(f"批量添加 LINE 好友")
    print(f"目标总数: {total} | 已完成: {done} | 待添加: {len(pending)}")
    print(f"间隔: {args.delay_min}-{args.delay_max} 秒")
    print(f"设备: {device}")
    print(f"=" * 60)

    if args.dry_run:
        print("\n📋 预览模式（不会实际添加）：")
        for i, tid in enumerate(pending, 1):
            greeting = random.choice(GREETINGS)
            print(f"  {i}. {tid} → {greeting[:40]}...")
        return

    print()
    for i, line_id in enumerate(pending, 1):
        greeting = random.choice(GREETINGS)
        timestamp = datetime.now().strftime("%H:%M:%S")

        print(f"[{timestamp}] ({i}/{len(pending)}) {line_id} ... ", end="", flush=True)

        success, detail = add_one(line_id, greeting, device)

        if success:
            print(f"✅ {detail}")
            results["completed"].append({
                "line_id": line_id,
                "time": datetime.now().isoformat(),
                "steps": detail
            })
        else:
            print(f"❌ {detail}")
            results["failed"].append({
                "line_id": line_id,
                "time": datetime.now().isoformat(),
                "reason": detail
            })

        save_results(results)

        # 最后一个不加延迟
        if i < len(pending):
            delay = random.randint(args.delay_min, args.delay_max)
            print(f"      等待 {delay} 秒...")
            time.sleep(delay)

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"完成！成功: {len(results['completed'])}, 失败: {len(results['failed'])}")
    if results['failed']:
        print(f"\n失败列表:")
        for f in results['failed']:
            print(f"  ❌ {f['line_id']} — {f['reason']}")
    print(f"结果已保存: {RESULT_FILE}")


if __name__ == "__main__":
    main()
