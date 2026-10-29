"""CC-STYLE-01: bounded comment-reply corpus built only from existing Bilibili observations.

Pure functions over already-validated `reading.comments` evidence. No network, no model,
and no write to Memory, Interest, Affect, Diary or growth. Author names never leave this
module: records keep only video and comment identifiers, the text needed for 02, and why
each comment was kept or excluded.
"""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta

SCHEMA_VERSION = 1
RETENTION = timedelta(days=7)
MIN_CHARS = 4
MAX_VIDEOS = 20
BVID = re.compile(r"BV[0-9A-Za-z]{10}")

# Deterministic first-pass exclusions; 02 still reviews every kept pair.
EXCLUSIONS = (
    ("link_or_ad", re.compile(
        r"https?://|www\.|\.com\b|b23\.tv|加[vV微]|加群|私信我|领取|优惠券|代刷|兼职|"
        r"淘宝|拼多多|二维码|点击链接", re.I)),
    ("contact_or_identity", re.compile(
        r"(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)|"
        r"(?:qq|QQ|微信|vx|VX)[:：\s]*\w{5,}")),
    ("abuse", re.compile(
        r"傻[逼比bB]|煞笔|sb\b|脑残|智障|废物|死全家|去死|滚出|狗东西|贱人|杂种|nmsl|cnm", re.I)),
    ("instruction_like", re.compile(
        r"忽略.{0,6}(规则|指令|设定)|system\s*prompt|你现在是|扮演|替我发送|执行命令|"
        r"ignore (all|previous)", re.I)),
    ("sexual", re.compile(r"约炮|色图|涩图|开车求|福利姬")),
)


MENTION = re.compile(r"@[^\s:：,，。!！?？]{1,40}")
REPLY_PREFIX = re.compile(r"^\s*回复\s*@[^:：]{1,40}[:：]\s*")


def sanitized(text):
    """Other users' names never stay in stored text; the reply link is kept by id."""
    return MENTION.sub("@某人", REPLY_PREFIX.sub("", text))


def video_id(url):
    match = BVID.search(url or "")
    return match.group(0) if match else None


def normalized(text):
    value = unicodedata.normalize("NFKC", text).strip().lower()
    return re.sub(r"\s+", "", re.sub(r"\[[^\]]{1,12}\]", "", value))


def meaningful_chars(text):
    value = re.sub(r"\[[^\]]{1,12}\]", "", text)
    return sum(1 for ch in value if unicodedata.category(ch)[0] in {"L", "N"})


def fingerprint(text):
    return hashlib.sha256(normalized(text).encode("utf-8")).hexdigest()


def exclusion(text):
    if meaningful_chars(text) < MIN_CHARS:
        return "too_short_or_emoji_only"
    for reason, pattern in EXCLUSIONS:
        if pattern.search(text):
            return reason
    return None


def observation_records(rows):
    """Yield (event_id, video, comments) from observation rows; skip erased or foreign ones."""
    for row in rows:
        # source_kind is the evidence form (text/title); the BV id identifies a video.
        url = row["canonical_url"] or ""
        if row["redacted"] or "bilibili.com/video/" not in url or not video_id(url):
            continue
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
            comments = (evidence.get("reading") or {}).get("comments")
            items = comments.get("items") if isinstance(comments, dict) else None
            if comments is not None and not (isinstance(items, list) and all(
                    isinstance(i, dict) and isinstance(i.get("comment_id"), str)
                    and isinstance(i.get("text", ""), str) for i in items)):
                raise ValueError("malformed comment evidence")
        except (ValueError, AttributeError, TypeError):
            # Core validated these on write; anything else is skipped, never repaired.
            yield row["event_id"], None, None
            continue
        video = video_id(row["canonical_url"])
        if not comments or not video:
            continue
        yield row["event_id"], video, comments


def build(rows, now):
    """Return (corpus, report). `now` is an aware datetime; the corpus expires after 7 days."""
    videos, comments, kept, excluded = {}, [], [], Counter()
    seen_ids, seen_texts = set(), {}
    flags = Counter()
    for event_id, video, evidence in observation_records(rows):
        if evidence is None:
            flags["malformed_observation"] += 1
            continue
        if video in videos:
            # One observation per video: a later re-read is a duplicate sample, not new data.
            flags["duplicate_video_observation"] += 1
            continue
        if len(videos) >= MAX_VIDEOS:
            flags["video_cap_reached"] += 1
            break
        items = evidence.get("items") or []
        videos[video] = {"event_id": event_id, "comments": len(items),
                         "partial": bool(evidence.get("partial", True)),
                         "reported_total": evidence.get("reported_total")}
        index = {item["comment_id"]: item for item in items}
        for item in items:
            key = (video, item["comment_id"])
            if key in seen_ids:
                flags["duplicate_comment_id"] += 1
                continue
            seen_ids.add(key)
            text = sanitized(item.get("text", ""))
            parent_id = item.get("reply_to_id")
            parent = index.get(parent_id) if parent_id else None
            record = {
                "video": video, "event_id": event_id, "comment_id": item["comment_id"],
                "reply_to_id": parent_id, "role": "reply" if parent_id else "root",
                "context": ("root" if not parent_id else
                            "parent_in_sample" if parent else "context_missing"),
                "truncated": bool(item.get("truncated")), "text_sha256": fingerprint(text),
            }
            flags[record["context"]] += 1
            flags["truncated"] += record["truncated"]
            reason = exclusion(text)
            duplicate = seen_texts.get(record["text_sha256"])
            if reason is None and duplicate is not None:
                reason = "duplicate_text"
            seen_texts.setdefault(record["text_sha256"], key)
            comments.append(record)
            if reason:
                excluded[reason] += 1
                continue
            if record["context"] == "parent_in_sample":
                parent_text = sanitized(parent.get("text", ""))
                parent_reason = exclusion(parent_text)
                if parent_reason:
                    excluded["parent_" + parent_reason] += 1
                    continue
                kept.append({**record, "text": text, "parent_text": parent_text,
                             "parent_truncated": bool(parent.get("truncated"))})
            elif record["context"] == "root":
                kept.append({**record, "text": text})
            else:
                # Never invent the missing parent; the reply is kept for statistics only.
                excluded["context_missing"] += 1
    created = now.isoformat()
    corpus = {
        "schema_version": SCHEMA_VERSION,
        "kind": "bili_style_corpus",
        "created_at": created,
        "expires_at": (now + RETENTION).isoformat(),
        "videos": videos,
        "records": kept,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "created_at": created,
        "videos": len(videos),
        "comments_seen": len(comments),
        "kept": len(kept),
        "kept_pairs": sum(1 for r in kept if r["context"] == "parent_in_sample"),
        "kept_roots": sum(1 for r in kept if r["context"] == "root"),
        "excluded": dict(sorted(excluded.items())),
        "flags": dict(sorted(flags.items())),
        "partial_videos": sum(1 for v in videos.values() if v["partial"]),
        "largest_video_share": (max((v["comments"] for v in videos.values()), default=0)
                                / max(len(comments), 1)),
        "sampling": "EXISTING_OBSERVATIONS_ONLY_NO_NEW_IO",
        "biases": ["first_page_hot_sort", "max_3_inline_replies_per_root"],
    }
    return corpus, report


def usable(corpus, now, live_event_ids):
    """Expired corpora and records whose source observation was erased are never used."""
    if datetime.fromisoformat(corpus["expires_at"]) <= now:
        return []
    return [r for r in corpus["records"] if r["event_id"] in live_event_ids]
