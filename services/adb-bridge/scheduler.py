"""
Scheduler — LINE 账号调度器

职责：
  1. 从数据库获取所有账号
  2. 自动过滤不可用账号
  3. 返回最适合执行任务的账号

过滤规则：
  • banned / disabled — 永久排除
  • dead — 设备已死，排除
  • login_error — 登录异常，排除
  • cooldown — 冷却中，排除
  • search_limit — 今日搜索次数用完，排除
  • daily_limit — 今日加好友数用完，排除（仅对 add_friend 任务）

选择策略：
  最久未使用（last_task_at 最早）的账号优先，均匀分摊工作量。

不负责 ADB，不负责 LINE 操作，不直接写数据库。
依赖 AccountManager 进行状态读写。
"""
from account_manager import AccountManager
import time


# 不可调度的账号状态（永久或需人工处理）
_BLOCKED_STATUSES = {"banned", "disabled", "dead", "login_error"}


class Scheduler:
    """LINE 账号调度器"""

    def __init__(self, account_manager: AccountManager = None):
        self.am = account_manager or AccountManager()

    # ─── 核心接口 ───

    def pick_account(self, task_type: str = "add_friend") -> dict | None:
        """
        返回最适合执行任务的账号。
        无可用账号时返回 None。

        返回格式: {device_id, addr, label, status, daily_success, daily_add_limit,
                   last_task_at, ...}
        """
        candidates = self._get_candidates(task_type)
        if not candidates:
            return None

        # 选择策略：最久未使用的优先（均匀分摊）
        candidates.sort(key=lambda a: a.get("last_task_at") or 0)
        return candidates[0]

    def get_available_count(self, task_type: str = "add_friend") -> int:
        """返回当前可执行该任务的账号数"""
        return len(self._get_candidates(task_type))

    def list_available(self, task_type: str = "add_friend") -> list[dict]:
        """返回所有可执行该任务的账号列表"""
        return self._get_candidates(task_type)

    def list_unavailable(self, task_type: str = "add_friend") -> list[dict]:
        """
        列出所有不可用的账号及原因。
        返回格式: [{device_id, status, reason, ...}]
        """
        accounts = self.am.list_accounts()
        result = []
        for a in accounts:
            ok, reason = self._check_account(a, task_type)
            if not ok:
                result.append({
                    "device_id": a["device_id"],
                    "label": a.get("label", ""),
                    "status": a.get("status", ""),
                    "reason": reason,
                    "daily_success": a.get("daily_success", 0),
                    "daily_add_limit": a.get("daily_add_limit", 0),
                    "daily_search": a.get("daily_search", 0),
                    "consecutive_fails": a.get("consecutive_fails", 0),
                    "cooldown_until": a.get("cooldown_until"),
                })
        return result

    # ─── 内部分拣逻辑 ───

    def _get_candidates(self, task_type: str) -> list[dict]:
        """获取所有可通过 _check_account 的候选账号"""
        accounts = self.am.list_accounts()
        candidates = []
        for a in accounts:
            ok, _ = self._check_account(a, task_type)
            if ok:
                candidates.append(a)
        return candidates

    def _check_account(self, account: dict,
                       task_type: str) -> tuple[bool, str]:
        """
        检查单个账号是否可执行指定任务。
        返回 (bool, reason)。

        检查顺序（从重到轻）：
          1. 账号不存在 → not_found
          2. banned / disabled / dead / login_error → 对应状态名
          3. cooldown 且未到期 → cooldown
          4. add_friend 任务: daily_success >= daily_add_limit → daily_limit
          5. 任何需要搜索的任务: daily_search >= daily_search_limit → search_limit
          6. 通过 → ok
        """
        if not account:
            return (False, "not_found")

        device_id = account.get("device_id", "?")
        status = account.get("status", "")

        # 1. 永久/需人工处理的状态
        if status in _BLOCKED_STATUSES:
            return (False, status)

        # 2. 冷却中
        if status == "cooldown":
            cooldown_until = account.get("cooldown_until")
            if cooldown_until and cooldown_until > time.time():
                remaining = int(cooldown_until - time.time())
                return (False, f"cooldown({remaining}s)")
            # 冷却已过期，允许执行（调用方应该先 exit_cooldown）

        # 3. 暂停
        if status == "paused":
            return (False, "paused")

        # 4. 每日加好友限额
        if task_type == "add_friend":
            daily_success = account.get("daily_success", 0)
            daily_add_limit = account.get("daily_add_limit", 10)
            if daily_success >= daily_add_limit:
                return (False, f"daily_limit({daily_success}/{daily_add_limit})")

        # 5. 搜索次数上限（add_friend / check_inbox 都需要搜索）
        if task_type in ("add_friend", "check_inbox", "search"):
            daily_search = account.get("daily_search", 0)
            daily_search_limit = account.get("daily_search_limit", 50)
            if daily_search >= daily_search_limit:
                return (False, f"search_limit({daily_search}/{daily_search_limit})")

        return (True, "ok")

    # ─── 便捷方法 ───

    def pick_next(self, task_type: str = "add_friend",
                  exclude: set = None) -> dict | None:
        """
        返回最优账号，支持排除列表。
        用于批量调度时跳过已分配过的账号。
        """
        candidates = self._get_candidates(task_type)
        if exclude:
            candidates = [c for c in candidates
                          if c.get("device_id") not in exclude]
        if not candidates:
            return None
        candidates.sort(key=lambda a: a.get("last_task_at") or 0)
        return candidates[0]

    def pick_multi(self, count: int,
                   task_type: str = "add_friend") -> list[dict]:
        """
        返回最多 count 个最优账号。
        用于并行调度多台设备。
        """
        candidates = self._get_candidates(task_type)
        candidates.sort(key=lambda a: a.get("last_task_at") or 0)
        return candidates[:count]

    def get_status_summary(self) -> dict:
        """
        返回调度状态概览。
        用于 /health 或日报。
        """
        accounts = self.am.list_accounts()
        summary = {
            "total": len(accounts),
            "available_add": 0,
            "available_reply": 0,
            "blocked": 0,
            "cooldown": 0,
            "daily_limit": 0,
            "search_limit": 0,
            "dead_login": 0,
            "paused": 0,
            "details": [],
        }
        for a in accounts:
            ok_add, reason_add = self._check_account(a, "add_friend")
            ok_reply, reason_reply = self._check_account(a, "check_chat")

            if ok_add:
                summary["available_add"] += 1
            if ok_reply:
                summary["available_reply"] += 1

            if reason_add != "ok":
                if reason_add in ("banned", "disabled", "dead", "login_error"):
                    summary["dead_login"] += 1
                elif reason_add.startswith("cooldown"):
                    summary["cooldown"] += 1
                elif reason_add.startswith("daily_limit"):
                    summary["daily_limit"] += 1
                elif reason_add.startswith("search_limit"):
                    summary["search_limit"] += 1
                elif reason_add == "paused":
                    summary["paused"] += 1
                else:
                    summary["blocked"] += 1

                summary["details"].append({
                    "device_id": a.get("device_id"),
                    "label": a.get("label", ""),
                    "status": a.get("status"),
                    "add_ok": ok_add,
                    "add_reason": reason_add,
                    "reply_ok": ok_reply,
                    "reply_reason": reason_reply,
                    "daily": f"{a.get('daily_success',0)}/{a.get('daily_add_limit',0)}",
                    "search": f"{a.get('daily_search',0)}/{a.get('daily_search_limit',0)}",
                })

        return summary
