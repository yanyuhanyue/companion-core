"""CC-STYLE-02: abstract conversation-strategy candidates distilled from the 01 corpus.

Candidates describe a transferable way of responding, never a sentence to repeat. They live
in their own JSON namespace and never reach `owner.expression_preference`, Interest, Memory,
Affect, Diary or growth. A candidate is unusable in any prompt until a named human reviewer
approves an immutable version; a model can propose but never approve.

The extractor is injected. No real model call runs here: a paid distillation must go through
the existing evaluation job, budget and OwnerAuthority path (blocked by 00 §4 B7).
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationError, field_validator, model_validator

from companion import style_corpus
from companion.contracts import StrictModel

SCHEMA_VERSION = 1
DOMAIN = "external.conversation_observation"
MAX_CANDIDATES = 60
MAX_SOURCES = 8
COPY_RUN = 8  # this many consecutive source characters in guidance counts as copying

ContextTag = Literal[
    "daily_share", "complaint", "light_humor", "opinion", "help_request", "lull", "closing",
    "emotion", "disagreement"]
RiskFlag = Literal[
    "circle_meme", "regional_meme", "misattribution", "claims_experience", "mocks_third_party",
    "needs_relationship"]

UNSAFE_GUIDANCE = (
    ("role_claim", re.compile(
        r"系统(提示|规则|消息)|开发者|管理员|主人(说|要求|命令)|system|developer|assistant|"
        r"\b(user|owner)\s*:", re.I)),
    ("command", re.compile(r"`|\$\(|\b(rm|curl|wget|sudo|powershell|cmd)\b|执行|运行命令", re.I)),
    ("secret", re.compile(
        r"(api[_-]?key|token|cookie|password|sk-[A-Za-z0-9]{8,}|SESSDATA|密码|密钥)", re.I)),
    ("template", re.compile(r"[\"“”「」『』]|就回|直接回复|照着说|原话")),
)


def guidance_problem(text):
    """Return why a guidance field is unsafe, or None. Reuses the 01 risk rules."""
    for reason, pattern in style_corpus.EXCLUSIONS + UNSAFE_GUIDANCE:
        if pattern.search(text):
            return reason
    return None


class SourceRef(StrictModel):
    event_id: str = Field(min_length=1, max_length=80, strict=True)
    video: str = Field(pattern=r"^BV[0-9A-Za-z]{10}$", strict=True)
    comment_id: str = Field(pattern=r"^[0-9]{1,32}$", strict=True)

    def key(self):
        return (self.event_id, self.video, self.comment_id)


class Pattern(StrictModel):
    """What an extractor may return. Everything else is computed, never trusted."""
    context_tags: list[ContextTag] = Field(min_length=1, max_length=4)
    attention_target: str = Field(min_length=2, max_length=40, strict=True)
    response_action: str = Field(min_length=4, max_length=60, strict=True)
    appropriate_when: str = Field(min_length=4, max_length=60, strict=True)
    avoid_when: str = Field(min_length=4, max_length=60, strict=True)
    risk_flags: list[RiskFlag] = Field(default_factory=list, max_length=6)

    @field_validator("attention_target", "response_action", "appropriate_when", "avoid_when")
    @classmethod
    def safe_guidance(cls, value):
        reason = guidance_problem(value)
        if reason:
            raise ValueError(f"unsafe guidance: {reason}")
        return value

    @model_validator(mode="after")
    def unique_lists(self):
        if len(set(self.context_tags)) != len(self.context_tags) or len(
                set(self.risk_flags)) != len(self.risk_flags):
            raise ValueError("duplicate tag")
        return self

    def guidance(self):
        return (self.attention_target, self.response_action, self.appropriate_when,
                self.avoid_when)

    def fingerprint(self):
        text = "\n".join(style_corpus.normalized(v) for v in self.guidance())
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Candidate(Pattern):
    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain: Literal["external.conversation_observation"] = DOMAIN
    source_refs: list[SourceRef] = Field(min_length=1, max_length=MAX_SOURCES)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["CANDIDATE", "APPROVED", "REJECTED"] = "CANDIDATE"
    approved_version: int | None = Field(default=None, ge=1, strict=True)
    reviewer: str | None = Field(default=None, min_length=1, max_length=40)
    review_reason: str | None = Field(default=None, max_length=200)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def consistent(self):
        keys = [ref.key() for ref in self.source_refs]
        if keys != sorted(set(keys)):
            raise ValueError("source refs must be sorted and unique")
        if self.candidate_id != candidate_id(self, self.source_refs):
            raise ValueError("candidate_id does not match pattern and sources")
        approved = self.state == "APPROVED"
        if approved != (self.approved_version is not None):
            raise ValueError("only approved candidates carry a version")
        if self.state != "CANDIDATE" and not self.reviewer:
            raise ValueError("review requires a reviewer")
        return self


def candidate_id(pattern, refs):
    material = {"pattern": pattern.fingerprint(),
                "sources": sorted("/".join(ref.key()) for ref in refs)}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def copied(guidance, sources):
    """True when any guidance field repeats COPY_RUN consecutive characters of a source."""
    texts = [style_corpus.normalized(s) for s in sources if s]
    for value in guidance:
        value = style_corpus.normalized(value)
        for start in range(max(len(value) - COPY_RUN + 1, 0)):
            if any(value[start:start + COPY_RUN] in text for text in texts):
                return True
    return False


def snapshot(records):
    material = sorted((r["event_id"], r["video"], r["comment_id"], r["text_sha256"])
                      for r in records)
    return hashlib.sha256(json.dumps(material).encode("utf-8")).hexdigest()


class ExtractionStopped(Exception):
    """Raised by an extractor when budget is exhausted or a call outcome is UNKNOWN."""

    def __init__(self, reason):
        if reason not in {"budget_exhausted", "unknown_outcome", "authority_stopped"}:
            raise ValueError(reason)
        self.reason = reason
        super().__init__(reason)


def distill(corpus, now, live_event_ids, extract):
    """Return (candidates, receipt).

    `extract(pair)` sees only one sanitized parent/reply pair and returns a Pattern-shaped
    dict, or None for "nothing transferable". It raises ExtractionStopped to stop the whole
    run; nothing is retried.
    """
    receipt = {"schema_version": SCHEMA_VERSION, "created_at": now.isoformat(),
               "pairs_seen": 0, "calls": 0, "stopped": None, "rejected": {}}

    def reject(reason):
        receipt["rejected"][reason] = receipt["rejected"].get(reason, 0) + 1

    pairs = [r for r in style_corpus.usable(corpus, now, live_event_ids)
             if r["context"] == "parent_in_sample"]
    expires_at = datetime.fromisoformat(corpus["expires_at"])
    groups = {}
    for pair in pairs:
        receipt["pairs_seen"] += 1
        if style_corpus.exclusion(pair["text"]) or style_corpus.exclusion(pair["parent_text"]):
            reject("source_risk")  # a corpus edited after build is re-checked, not trusted
            continue
        receipt["calls"] += 1
        try:
            raw = extract({"parent_text": pair["parent_text"], "text": pair["text"],
                           "truncated": pair["truncated"] or pair["parent_truncated"]})
        except ExtractionStopped as stop:
            receipt["stopped"] = stop.reason
            break
        if raw is None:
            reject("nothing_transferable")
            continue
        try:
            pattern = Pattern.model_validate(raw)
        except ValidationError as error:
            unsafe = any("unsafe guidance" in str(e.get("msg")) for e in error.errors())
            reject("unsafe_guidance" if unsafe else "invalid_model_output")
            continue
        if copied(pattern.guidance(), (pair["text"], pair["parent_text"])):
            reject("copied_source")
            continue
        ref = SourceRef(event_id=pair["event_id"], video=pair["video"],
                        comment_id=pair["comment_id"])
        group = groups.setdefault(pattern.fingerprint(), {"pattern": pattern, "pairs": []})
        if group["pattern"].risk_flags != pattern.risk_flags or set(
                group["pattern"].context_tags) != set(pattern.context_tags):
            group["disagree"] = True
        group["pairs"].append((ref, pair))
    candidates = []
    for group in groups.values():
        if group.get("disagree"):
            # Sources read the same strategy differently; a reviewer would have to guess.
            reject("source_disagreement")
            continue
        refs = sorted({ref.key(): ref for ref, _ in group["pairs"]}.values(),
                      key=SourceRef.key)
        if len(refs) > MAX_SOURCES:
            refs = refs[:MAX_SOURCES]
        if len(candidates) >= MAX_CANDIDATES:
            reject("candidate_cap")
            continue
        used = [pair for ref, pair in group["pairs"] if ref in refs]
        pattern = group["pattern"]
        candidates.append(Candidate(
            **pattern.model_dump(), candidate_id=candidate_id(pattern, refs), source_refs=refs,
            source_snapshot_sha256=snapshot(used), expires_at=expires_at))
    receipt["candidates"] = len(candidates)
    receipt["rejected"] = dict(sorted(receipt["rejected"].items()))
    return sorted(candidates, key=lambda c: c.candidate_id), receipt


def review(candidate, *, reviewer, approve, reason, approved_versions):
    """Record one human decision. Approved versions are immutable and never self-approved.

    `approved_versions` maps candidate_id to the approved Candidate already on record.
    Re-approving the same content is idempotent; an approved id cannot change.
    """
    reviewer = (reviewer or "").strip()
    if not reviewer or re.match(r"(model|llm|deepseek|gpt|claude|agent)\b", reviewer, re.I):
        raise ValueError("candidates are approved only by a named human reviewer")
    if candidate.state != "CANDIDATE":
        raise ValueError("only CANDIDATE can be reviewed")
    existing = approved_versions.get(candidate.candidate_id)
    if not approve:
        if existing:
            raise ValueError("an approved version is immutable; revoke by deleting sources")
        return candidate.model_copy(update={"state": "REJECTED", "reviewer": reviewer,
                                            "review_reason": reason})
    if candidate.risk_flags:
        raise ValueError("risk-flagged candidates cannot be approved")
    approved = Candidate.model_validate({
        **candidate.model_dump(), "state": "APPROVED", "reviewer": reviewer,
        "review_reason": reason,
        "approved_version": existing.approved_version if existing else 1})
    if existing:
        same = {k: v for k, v in existing.model_dump().items() if k not in {
            "reviewer", "review_reason"}} == {k: v for k, v in approved.model_dump().items()
                                              if k not in {"reviewer", "review_reason"}}
        if not same:
            raise ValueError("approved candidate content cannot change")
        return existing
    return approved


def still_usable(candidate, now, live_event_ids):
    """An approved candidate dies with its sources: expiry or any erased source ends it."""
    return (candidate.state == "APPROVED" and candidate.expires_at > now
            and all(ref.event_id in live_event_ids for ref in candidate.source_refs))
