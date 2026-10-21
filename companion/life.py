"""Owner-facing commands and bounded context for v0.5 capabilities."""

import json
import re
from datetime import datetime

from companion.commands import command_parts
from companion.contracts import CoreError
from companion.goals import CommitmentRequest, GoalProposal, GoalScope
from companion.store import after

COMMANDS = (
    "目标列表",
    "承诺列表",
    "反思列表",
    "当前交互状态",
    "为什么形成偏好",
    "暂停自主活动",
    "恢复自主活动",
    "接受目标",
    "取消目标",
    "安排任务",
)
STATES = {
    "CANDIDATE": "候选",
    "ACCEPTED": "已接受",
    "QUEUED": "等待处理",
    "RUNNING": "处理中",
    "DONE": "已完成",
    "WAITING_INPUT": "需要补充来源",
    "WAITING_RESOURCE": "等待资源",
    "RETRY_WAIT": "等待重试",
    "FAILED": "未完成",
    "CANCELLED": "已取消",
    "ACKNOWLEDGED": "已记录",
    "IN_PROGRESS": "处理或反馈中",
    "FULFILLED": "已完成并反馈",
    "EXPLAINED_FAILURE": "已说明未完成原因",
}


def is_life_command(text):
    return command_parts(text)[0] in COMMANDS


class LifeService:
    def __init__(self, store, settings, goals, reflections, affect, clock):
        self.store, self.settings, self.clock = store, settings, clock
        self.goals, self.reflections, self.affect = goals, reflections, affect

    def command(self, text, event_id):
        command, arguments = command_parts(text)
        if command not in COMMANDS:
            return None
        text = command + (" " + arguments if arguments else "")
        if text in {"暂停自主活动", "恢复自主活动"}:
            self.goals.pause_autonomy(text.startswith("暂停"))
            return (
                "自主探索已暂停，已接受的任务继续按约定处理。"
                if text.startswith("暂停")
                else "自主探索已恢复，仍遵循预算和免打扰设置。"
            )
        if text in {"目标列表", "承诺列表"}:
            rows = self.goals.recent()
            if text == "承诺列表":
                rows = [row for row in rows if row["kind"] == "commitment"]
            return (
                "\n".join(
                    f"{row['goal_id']} · {row['topic']} · "
                    f"{STATES[row['commitment_state'] or row['state']]}"
                    for row in rows
                )
                or "目前没有待跟进的目标。"
            )
        if text == "反思列表":
            return (
                "\n".join(
                    "待核对的想法：" + row["content"] for row in self.reflections.recent()[:5]
                )
                or "目前没有待核对的反思。"
            )
        if text == "当前交互状态":
            current = self.affect.snapshot()
            if not current["evidence"]:
                return "目前没有额外的临时沟通偏好，按通常方式交流。"
            labels = {
                "concise": "简短表达",
                "detailed": "详细解释",
                "stepwise": "分步骤说明",
                "pause": "暂缓这个话题",
                "continue": "继续当前话题",
                "open": "尝试新话题",
            }
            return (
                "暂时按你的明确反馈："
                + "、".join(labels[value] for value in current["state"].values() if value in labels)
                + "。这些偏好会在到期后回到中性。"
            )
        if text == "为什么形成偏好":
            from companion.interest import profile

            interests = profile(self.store, self.settings, self.clock(), self.settings.owner_qq_id)[
                "self.interest"
            ]
            rows = [
                {"topic": item["topic"], **evidence}
                for item in interests
                for evidence in item["evidence"]
            ][:5]
            return (
                "\n".join(
                    f"持续探索的话题：{row['topic']}；依据 {row['occurred_at'][:10]} "
                    f"的内容记录 {row['canonical_url']}"
                    for row in rows
                )
                or "目前还没有足够的自主探索记录来形成偏好。"
            )
        decision = re.fullmatch(r"(接受目标|取消目标)\s+(goal_[a-f0-9]+)", text)
        if decision:
            try:
                result = (
                    self.goals.accept(decision[2])
                    if decision[1] == "接受目标"
                    else self.goals.cancel(decision[2])
                )
                return "目标已更新：" + STATES[result["state"]] + "。"
            except CoreError as error:
                return "目标未更新（" + error.code + "）。"
        task = re.fullmatch(r"安排任务\s+(https://\S+)(?:\s+(.{2,300}))?", text)
        if task:
            try:
                request = CommitmentRequest(
                    proposal=GoalProposal(
                        topic=task[2] or "阅读这篇内容",
                        source_event_ids=[event_id],
                        scope=GoalScope(operation="read_content", url=task[1]),
                    ),
                    accepted_event_id=event_id,
                    deadline=datetime.fromisoformat(after(self.clock(), 86400)),
                )
                self.goals.accept_commitment(request)
                return (
                    "已记录这项阅读任务，截止时间为明天此时。完成或无法完成时，我会说明结果。"
                    if self.settings.exploration_enabled
                    else "已记录这项任务，但内容读取目前不可用，正在等待资源；"
                    "明天此时前会说明结果。"
                )
            except ValueError:
                return "请提供有效的 HTTPS 内容链接，格式：/安排任务 链接 主题。"
            except CoreError as error:
                return "这项任务尚未接受（" + error.code + "）。"
        return None

    def context(self):
        if not self.settings.goals_enabled:
            return []
        items = []
        for goal in self.goals.recent()[:3]:
            item = {
                "topic": goal["topic"],
                "state": goal["state"],
                "commitment_state": goal["commitment_state"],
            }
            if goal["result_event_id"]:
                event = self.store.one(
                    "SELECT body_json FROM events WHERE event_id=? AND redacted=0",
                    (goal["result_event_id"],),
                )
                if event:
                    item["verified_result"] = json.loads(event["body_json"])["text"][:300]
                    item["result_event_id"] = goal["result_event_id"]
            items.append(item)
        return items
