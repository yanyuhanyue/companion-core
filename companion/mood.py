"""Rebuildable companion PAD expression hints over retained native evidence.

No model, event writer, scheduler, personality store or execution authority lives here.
Weights are conservative product rules, not measurements of anyone's emotions.
"""

import json
import re
from contextlib import nullcontext
from datetime import UTC, datetime

from companion import goal_plan, owner_authority
from companion.contracts import CoreError
from companion.memory_authority import source_allowed
from companion.memory_context import MemorySources, check_version, read_version
from companion.memory_projections import MemoryProjections
from companion.paid_budget import sha
from companion.store import after, encode, uid

VERSION = "mood-1"
RESET_KEY = "mood_reset_at"
PAUSE_KEY = "mood_paused"
DIMENSIONS = ("pleasure", "arousal", "dominance")
HALF_LIVES = {"immediate": 3 * 3600, "undertone": 3 * 86400}
WINDOW = 14 * 86400
LIMIT = 256
# Whole original messages only. Quotations, commands and generated replies are excluded.
FEEDBACK = {
    **{
        prefix + ending: ("HELPFUL_FEEDBACK", (0.08, 0.02, 0.02))
        for prefix in ("你刚才的回答", "这个回答")
        for ending in ("很有帮助", "帮到我了", "让我很开心")
    },
    **{
        prefix + ending: ("THANKS_FOR_HELP", (0.06, 0.01, 0.02))
        for prefix in ("谢谢你的", "谢谢你刚才的")
        for ending in ("帮助", "回答")
    },
    **{
        prefix + ending: ("UNHELPFUL_FEEDBACK", (-0.04, 0.01, -0.02))
        for prefix in ("你刚才的回答", "这个回答")
        for ending in ("没帮上忙", "让我有点失望", "让我不舒服")
    },
}
FILTER_RULES = (
    "这是小肥鱼自身的暂时表达参考，不是主人的情绪、固定人格或真实情绪体验的证明。"
    "仅可轻微影响措辞、关注点和表达意愿；不必提心情或每轮表演感受，不指定回复。"
    "优先理解当前语境和主人明确偏好；严肃求助、危险、隐私及主人要求暂停时不用心情滤镜。"
    "不覆盖事实、理性判断、承诺、权限、预算或消息次数。"
    "来源只说明记录中的互动或线上阅读，不推导被冷落、依恋或线下经历。"
)


def stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("mood timestamp requires timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def reset_target(request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        raise CoreError("MOOD_RESET_ID_REQUIRED", 400)
    return "mood_reset:" + request_id


def reset_record(sequence, request_id, now):
    return {
        "seq": sequence,
        "erasure_id": uid("erase"),
        "memory_id": reset_target(request_id),
        "memory_ids": [],
        "event_ids": [],
        "at": stamp(now),
        "target_type": "mood_reset",
    }


def validate_reset_record(record):
    try:
        if (
            record.get("target_type") != "mood_reset"
            or record.get("event_ids") != []
            or record.get("memory_ids") != []
            or not record["memory_id"].startswith("mood_reset:")
        ):
            raise ValueError("invalid mood reset scope")
        reset_target(record["memory_id"][len("mood_reset:") :])
        stamp(record["at"])
    except (CoreError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("invalid mood reset") from error


def reset_at(store):
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (RESET_KEY,))
    try:
        return stamp(row["value"]) if row else None
    except (TypeError, ValueError) as error:
        raise CoreError("MOOD_RESET_INVALID", 503) from error


def apply_reset(store, record):
    from companion.privacy import advance_epoch

    if not store.db.in_transaction:
        raise RuntimeError("MOOD_RESET_REQUIRES_TRANSACTION")
    validate_reset_record(record)
    previous = reset_at(store)
    cutoff = max(stamp(record["at"]), previous or stamp(record["at"]))
    if cutoff != previous:
        store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)", (RESET_KEY, cutoff))
        advance_epoch(store)
    return {"state": "RESET", "reset_at": cutoff}


def vector(values):
    return dict(zip(DIMENSIONS, (round(x, 6) for x in values), strict=True))


def fold(signals, at):
    """Bounded impulses with time-based exponential relaxation; replay is deterministic."""
    values = {name: [0.0, 0.0, 0.0] for name in HALF_LIVES}
    previous = at
    seen = set()
    for item in signals:
        time = item["at"]
        if seen:
            seconds = (
                datetime.fromisoformat(time) - datetime.fromisoformat(previous)
            ).total_seconds()
            for layer, half in HALF_LIVES.items():
                values[layer] = [x * 2 ** (-seconds / half) for x in values[layer]]
        seen.add(item["independent_id"])
        for i, delta in enumerate(item["delta"]):
            # One event never changes a dimension by more than .08; saturation is smooth.
            values["immediate"][i] += delta * (1 - abs(values["immediate"][i]) / 0.35)
            if len(seen) >= 3:
                values["undertone"][i] += delta * 0.12 * (1 - abs(values["undertone"][i]) / 0.15)
        previous = time
    seconds = max(
        0.0, (datetime.fromisoformat(at) - datetime.fromisoformat(previous)).total_seconds()
    )
    for layer, half in HALF_LIVES.items():
        values[layer] = [x * 2 ** (-seconds / half) for x in values[layer]]
    return {
        **{name: vector(value) for name, value in values.items()},
        "expression": vector([values["immediate"][i] + values["undertone"][i] for i in range(3)]),
    }


class MoodService:
    def __init__(self, memory, clock):
        self.memory, self.clock = memory, clock
        self.store, self.settings = memory.store, memory.settings

    def _permitted(self, purpose):
        if not owner_authority.required(self.store, self.settings):
            return True
        try:
            owner_authority.current(self.store, self.settings, self.clock, purpose=purpose)
            return True
        except CoreError:
            return False

    def _lineage(self, key, sources):
        rows = list(sources._lineage(key))
        if not MemoryProjections._live_lineage(rows, sources):
            return None
        for row in rows:
            if row["source"] == "qq.onebot" and not source_allowed(
                self.store,
                self.settings,
                self.clock,
                row["event_id"],
                "expression",
            ):
                return None
        return rows

    def _signals(self, at, allowed):
        s, cfg = self.store, self.settings
        sources = MemorySources(self.memory, self.clock())
        if owner_authority.required(s, cfg):
            view = owner_authority.current(s, cfg, self.clock, purpose="expression")
            sources.cutoff = max(sources.cutoff, datetime.fromisoformat(after(
                self.clock(), -view.policy.source_retention_days * 86400)))
        cutoff = max(after(at, -WINDOW), reset_at(s) or after(at, -WINDOW))
        candidates = s.rows(
            "SELECT e.event_id,e.occurred_at AS at,'feedback' AS kind FROM events e "
            "WHERE e.source='qq.onebot' AND e.actor_id='owner' AND e.redacted=0 "
            "AND e.event_type='message.received' AND e.ingested_at>? AND e.ingested_at<=? "
            "AND e.occurred_at>? AND e.occurred_at<=? "
            "AND length(e.body_json)<=16000 AND rtrim(trim(json_extract(e.body_json,'$.text')),"
            "'。！! ') IN (SELECT value FROM json_each(?)) UNION ALL "
            "SELECT w.source_event_id,w.updated_at,'watch' FROM watch_ledger w "
            "WHERE w.state='JUDGED' AND w.rating<>0 AND w.depth<>'title_only' "
            "AND w.updated_at>? AND w.updated_at<=? UNION ALL "
            "SELECT g.result_event_id,g.updated_at,'goal' FROM goals g WHERE g.state='DONE' "
            "AND g.redacted=0 AND g.result_event_id IS NOT NULL AND g.updated_at>? "
            "AND g.updated_at<=? ORDER BY at DESC,event_id,kind LIMIT ?",
            (cutoff, at, cutoff, at, encode(list(FEEDBACK)), cutoff, at, cutoff, at, LIMIT + 1),
        )
        signals, used = [], set()
        for candidate in candidates[:LIMIT]:
            key, kind = candidate["event_id"], candidate["kind"]
            lineage = self._lineage(key, sources)
            if not lineage:
                continue
            event = next(row for row in lineage if row["event_id"] == key)
            # Reset and lookback follow the original event too: a delayed review cannot revive it.
            if (not cutoff < stamp(event["ingested_at"]) <= at
                or not cutoff < stamp(event["occurred_at"]) <= at):
                continue
            body = json.loads(event["body_json"])
            identity, detail, reason, delta = key, None, None, None
            if kind == "feedback":
                if (
                    event["actor_id"] != "owner"
                    or body.get("control_command")
                    or body.get("diary_command")
                    or body.get("quote")
                    or body.get("rich")
                ):
                    continue
                text = body.get("text", "")
                if not isinstance(text, str):
                    continue
                rule = FEEDBACK.get(text.strip().rstrip("。！! "))
                if not rule:
                    continue
                # Feedback refers to an actual delivered reply in this conversation.
                reply = s.one(
                    "SELECT o.* FROM outbox o JOIN events original "
                    "ON original.event_id=o.origin_event_id "
                    "WHERE o.conversation_id=? AND o.state='SENT' "
                    "AND o.action_type='qq.private.reply' AND o.sent_at<=? "
                    "AND original.rowid<(SELECT rowid FROM events WHERE event_id=?) "
                    "ORDER BY o.sent_at DESC,o.rowid DESC LIMIT 1",
                    (event["conversation_id"], candidate["at"], key),
                )
                if not reply or not (prior := self._lineage(reply["origin_event_id"], sources)):
                    continue
                # A possibly delivered newer reply makes the feedback target ambiguous.
                # Never silently attribute it to an older known SENT reply instead.
                uncertain = s.one(
                    "SELECT 1 FROM outbox o JOIN events original "
                    "ON original.event_id=o.origin_event_id WHERE o.conversation_id=? "
                    "AND o.action_type='qq.private.reply' "
                    "AND o.state IN ('SENDING','DELIVERY_UNCERTAIN') "
                    "AND o.created_at>=? AND o.created_at<=? "
                    "AND original.rowid<(SELECT rowid FROM events WHERE event_id=?) LIMIT 1",
                    (event["conversation_id"], reply["created_at"], candidate["at"], key),
                )
                if uncertain:
                    continue
                lineage += prior
                identity = "reply:" + reply["message_id"]
                detail, reason, delta = sha(reply), rule[0], rule[1]
            elif kind == "watch":
                if not self._permitted("exploration"):
                    continue
                detail = s.one("SELECT * FROM watch_ledger WHERE source_event_id=?", (key,))
                observation = s.one("SELECT * FROM observations WHERE event_id=?", (key,))
                usage = s.one(
                    "SELECT state,scenario FROM model_usage WHERE request_id=?",
                    (detail["model_request_id"],),
                )
                if (
                    event["source"] not in {"observation.bilibili", "observation.heybox"}
                    or not observation
                    or observation["redacted"]
                    or observation["result_status"] not in {"FETCHED", "WATCHED"}
                    or not observation["source_sha256"]
                    or not observation["canonical_url"]
                    or not usage
                    or usage["state"] != "SETTLED"
                    or usage["scenario"] != "watch_review"
                    or type(detail["rating"]) is not int
                    or not -2 <= detail["rating"] <= 2
                ):
                    continue
                identity = "read:" + observation["canonical_url"]
                reason, delta = "COMPLETED_READ_REVIEW", (detail["rating"] * 0.03, 0.0, 0.0)
                detail = sha([detail, observation])
            else:
                if not self._permitted("goals"):
                    continue
                from companion.goals import GoalEngine

                engine = GoalEngine(s, cfg, self.memory.ledger, self.clock)
                projection = MemoryProjections(self.memory)
                goals = s.rows(
                    "SELECT * FROM goals WHERE result_event_id=? AND state='DONE' "
                    "AND redacted=0 ORDER BY goal_id",
                    (key,),
                )
                found = None
                for goal in goals:
                    roots = engine.source_ids(goal["goal_id"])
                    steps = s.rows(
                        "SELECT * FROM goal_steps WHERE goal_id=? ORDER BY position",
                        (goal["goal_id"],),
                    )
                    if (
                        not roots
                        or not steps
                        or goal_plan.unknown(engine, goal["goal_id"])
                        or [x["position"] for x in steps] != list(range(1, len(steps) + 1))
                        or steps[-1]["outcome_event_id"] != key
                        or not all(
                            projection._completed(goal, step, roots, sources, allowed)
                            for step in steps
                        )
                    ):
                        continue
                    expanded = [self._lineage(root, sources) for root in roots]
                    expanded += [self._lineage(step["outcome_event_id"], sources) for step in steps]
                    if any(not value for value in expanded):
                        continue
                    lineage += [row for value in expanded for row in value]
                    found = sha([goal, steps])
                    break
                if not found:
                    continue
                receipt = s.one("SELECT canonical_url FROM observations WHERE event_id=?", (key,))
                identity, detail = "read:" + receipt["canonical_url"], found
                reason, delta = "VERIFIED_GOAL_COMPLETION", (0.03, 0.0, 0.04)
            ids = sorted({row["event_id"] for row in lineage})
            if allowed is not None and not set(ids) <= set(allowed):
                continue
            signal = {
                "event_id": key,
                "source_event_ids": ids,
                "kind": kind,
                "at": stamp(candidate["at"]),
                "reason": reason,
                "delta": list(delta),
                "independent_id": identity,
                "fingerprint": sha([lineage, detail]),
            }
            if (kind, identity) not in used:
                signals.append(signal)
                used.add((kind, identity))
        # The same observation's review and verified goal result form one bounded impulse.
        merged = {}
        for item in signals:
            key = item["event_id"]
            if key in merged:
                prior = merged[key]
                prior["delta"] = [
                    max(-0.08, min(0.08, a + b))
                    for a, b in zip(prior["delta"], item["delta"], strict=True)
                ]
                prior["source_event_ids"] = sorted(
                    set(prior["source_event_ids"] + item["source_event_ids"])
                )
                prior["fingerprint"] = sha([prior["fingerprint"], item["fingerprint"]])
                prior["at"] = max(prior["at"], item["at"])
                prior["reason"], prior["kind"] = "REVIEWED_GOAL_COMPLETION", "experience"
            else:
                merged[key] = item
        return sorted(
            merged.values(), key=lambda item: (item["at"], item["event_id"], item["kind"])
        ), len(candidates) > LIMIT

    def snapshot(self, *, at=None, allowed=None):
        at = stamp(at or self.clock())
        if at > stamp(self.clock()):
            raise CoreError("MOOD_TIME_INVALID", 400)
        captured = read_version(self.memory)
        paused = self.store.one(
            "SELECT 1 FROM runtime_meta WHERE key=? AND value='1'", (PAUSE_KEY,)
        )
        enabled = bool(self.settings.mood_enabled)
        active = enabled and not paused and self._permitted("expression")
        signals, truncated = self._signals(at, allowed) if active else ([], False)
        # Repeated evidence from the same real event cannot manufacture a lasting undertone.
        state = fold(signals, at)
        if truncated:
            state["undertone"] = vector([0.0, 0.0, 0.0])
            state["expression"] = state["immediate"].copy()
        result = {
            "version": VERSION,
            "evaluated_at": at,
            "enabled": enabled,
            "paused": bool(paused),
            "active": active,
            "reset_at": reset_at(self.store),
            **state,
            "signals": signals,
            "truncated": truncated,
            "confidence": "limited" if signals else "unknown",
            "half_life_seconds": HALF_LIVES,
            "source_event_ids": sorted(
                {key for item in signals for key in item["source_event_ids"]}
            ),
            "allowed": sorted(allowed) if allowed is not None else None,
        }
        result["trend"] = [
            {"at": point, **fold([s for s in signals if s["at"] <= point], point)}
            for point in (after(at, -86400), after(at, -21600), after(at, -3600), at)
        ]
        if truncated:
            for point in result["trend"]:
                point["undertone"] = vector([0., 0., 0.])
                point["expression"] = point["immediate"].copy()
        check_version(self.memory, captured)
        return result

    def revalidate(self, proof):
        if self.snapshot(at=proof["evaluated_at"], allowed=proof["allowed"]) != proof:
            raise CoreError("PRIVACY_CHANGED", 409)

    def reference(self, *, at=None, allowed=None, text=""):
        from companion.expression import SERIOUS
        from companion.privacy import sensitive

        if SERIOUS.search(text) or sensitive(text):
            return None
        view = self.snapshot(at=at, allowed=allowed)
        if not view["signals"] or not any(view["expression"].values()):
            return None
        return {
            "rules": FILTER_RULES,
            "state": view["expression"],
            "immediate": view["immediate"],
            "undertone": view["undertone"],
            "confidence": view["confidence"],
            "evaluated_at": view["evaluated_at"],
            "causes": [
                {key: item[key] for key in ("event_id", "at", "reason", "source_event_ids")}
                for item in view["signals"][-8:]
            ],
            "proof": view,
        }

    def control(self, action, requester, *, request_id=None):
        if requester != self.settings.owner_qq_id:
            raise CoreError("OWNER_REQUIRED", 403)
        if action not in {"pause", "resume", "clear", "recompute"}:
            raise CoreError("MOOD_ACTION_INVALID", 400)
        with nullcontext() if self.store.db.in_transaction else self.store.transaction():
            if action == "clear":
                return self.memory.ledger.reset_mood(self.store, request_id, self.clock())
            if action in {"pause", "resume"}:
                from companion.privacy import advance_epoch

                self.store.execute(
                    "INSERT OR REPLACE INTO runtime_meta VALUES (?,?)",
                    (PAUSE_KEY, "1" if action == "pause" else "0"),
                )
                advance_epoch(self.store)
            return {
                "state": {"pause": "PAUSED", "resume": "ACTIVE", "recompute": "RECOMPUTED"}[action],
                "mood": self.snapshot(),
            }


def service_for_store(store, settings, clock, ledger=None):
    from companion.memory import Memory
    from companion.privacy import ErasureLedger

    return MoodService(
        Memory(store, settings, ledger or ErasureLedger(settings.erasure_ledger)), clock
    )
