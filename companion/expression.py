"""Optional expression hints; the existing outbox owns delivery and erasure."""

import json
import re
from contextlib import nullcontext
from typing import Literal

from pydantic import Field

from companion.contracts import CoreError, StrictModel
from companion.store import after, encode
from companion.transport import digest, enqueue

MARKER = re.compile(r"⬡([^⬡\r\n]{1,80})⬡")
CATEGORY = re.compile(r"[a-z][a-z0-9_-]{0,31}")
SERIOUS = re.compile(
    r"严肃|认真说|不要.{0,4}表情|别.{0,4}表情|不发.{0,4}表情|"
    r"自杀|自残|想死|不想活|伤害自己|去世|逝世|葬礼|丧亲|失去亲人|"
    r"急救|急诊|住院|癌症|确诊|抑郁|焦虑症|惊恐|很难过|很痛苦|崩溃|"
    r"分手|失恋|家暴|性侵|诈骗|报警|起诉|法律咨询|医疗|诊断|药物|"
    r"\b(?:suicid\w*|self[- ]harm|funeral|grief|bereave\w*|hospital|diagnos\w*|"
    r"medical|abuse|rape|panic attack|no (?:memes|emoji)|serious(?:ly)?)\b",
    re.IGNORECASE,
)


class ImageIntent(StrictModel):
    source: Literal["astrbot.meme_manager"] = "astrbot.meme_manager"
    category: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class ResponseIntent(StrictModel):
    text: str = Field(min_length=1, max_length=20000)
    image: ImageIntent | None = None


class ExpressionEngine:
    def __init__(self, store, settings, clock):
        self.store, self.settings, self.clock = store, settings, clock

    def categories(self):
        configured = self.settings.expression_meme_categories
        return tuple(dict.fromkeys(c for c in configured if CATEGORY.fullmatch(c)))[:12]

    def instruction(self):
        if not self.settings.expression_enabled:
            return ""
        return (
            "表达自然，按当下内容选择长短，不必固定开场、重复总结或刻意卖萌。"
            "表达风格不改变事实、权限和承诺；不要把表达选择当成真实情绪。"
            "每次必须有完整文字回复。仅在轻松且合适时，可在末尾附一个表情标记"
            "⬡类别⬡，也可以完全不用。可用类别："
            + ", ".join(self.categories())
            + "。严肃、痛苦、医疗、法律或用户拒绝表情的语境只回复文字。"
            "不要输出图片网址、文件路径或声称图片已发送。"
        )

    def intent(self, event_id, reply, now=None):
        now = now or self.clock()
        if not self.settings.expression_enabled:
            return ResponseIntent(text=reply)
        markers = list(MARKER.finditer(reply))
        text = MARKER.sub("", reply).strip()
        if not text:
            raise CoreError("EXPRESSION_TEXT_REQUIRED", 422)
        intent = ResponseIntent(text=text)
        if (
            len(markers) != 1
            or markers[0].end() != len(reply.rstrip())
            or markers[0].group(1) not in self.categories()
        ):
            return intent
        event = self.store.one(
            "SELECT e.*,c.scope,c.owner_actor_id FROM events e JOIN conversations c "
            "USING(conversation_id) WHERE e.event_id=? AND e.redacted=0",
            (event_id,),
        )
        if not event or (
            event["actor_id"],
            event["source"],
            event["scope"],
            event["owner_actor_id"],
        ) != ("owner", "qq.onebot", "private", "owner"):
            return intent
        body = json.loads(event["body_json"])
        if body.get("diary_command") or body.get("control_command"):
            return intent
        recent = self.store.rows(
            "SELECT body_json FROM events WHERE conversation_id=? AND actor_id='owner' "
            "AND redacted=0 AND rowid<=(SELECT rowid FROM events WHERE event_id=?) "
            "ORDER BY rowid DESC LIMIT 6",
            (event["conversation_id"], event_id),
        )
        context = "\n".join(json.loads(row["body_json"]).get("text", "") for row in recent)
        if SERIOUS.search(context + "\n" + text):
            return intent
        cooldown = self.settings.expression_cooldown_minutes
        if self.store.one(
            "SELECT 1 FROM outbox WHERE conversation_id=? "
            "AND state NOT IN ('CANCELLED','FAILED') AND created_at>=? "
            "AND json_extract(content_json,'$.image.source')='astrbot.meme_manager' LIMIT 1",
            (event["conversation_id"], after(now, -cooldown * 60)),
        ):
            return intent
        rows = self.store.rows(
            "SELECT content_json FROM outbox WHERE conversation_id=? "
            "AND state NOT IN ('CANCELLED','FAILED') ORDER BY rowid DESC LIMIT 8",
            (event["conversation_id"],),
        )
        category = markers[0].group(1)
        for row in rows:
            image = json.loads(row["content_json"]).get("image")
            if not isinstance(image, dict):
                continue
            # Count ordinary replies too, so a small category set can become eligible again.
            if image.get("category") == category:
                return intent
        return intent.model_copy(update={"image": ImageIntent(category=category)})

    def enqueue(self, event_id, conversation_id, reply, now, action="qq.private.reply", key=None):
        """Freeze one text/image intent atomically under the existing delivery key."""
        key = key or f"reply:{event_id}:v1"
        with nullcontext() if self.store.db.in_transaction else self.store.transaction():
            previous = self.store.one("SELECT message_id FROM outbox WHERE delivery_key=?", (key,))
            if previous:
                return previous["message_id"]
            event = self.store.one(
                "SELECT conversation_id FROM events WHERE event_id=? AND redacted=0", (event_id,)
            )
            if not event:
                raise CoreError("PRIVACY_CHANGED", 503)
            if event["conversation_id"] != conversation_id:
                raise CoreError("EXPRESSION_CONVERSATION_MISMATCH", 400)
            if not self.settings.expression_enabled:
                return enqueue(
                    self.store, event_id, conversation_id, reply, now, action=action, key=key
                )
            intent = self.intent(event_id, reply, now)
            message_id = enqueue(
                self.store, event_id, conversation_id, intent.text, now, action=action, key=key
            )
            if intent.image is not None and action == "qq.private.reply":
                content = intent.model_dump(exclude_none=True)
                self.store.execute(
                    "UPDATE outbox SET content_json=?,content_sha256=? WHERE message_id=?",
                    (encode(content), digest(content), message_id),
                )
            return message_id
