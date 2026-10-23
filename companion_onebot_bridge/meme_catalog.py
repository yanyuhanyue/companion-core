"""Local semantic offers and immutable media bindings; never sends or authorizes I/O.

The caller supplies the existing Bridge spool SQLite connection and serialized ownership.
No private text is accepted: intents are a small expression vocabulary. All external
metadata is untrusted data, and must be serialized as such in the reply planner.
"""

import hashlib
import json
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .meme_library import CATEGORY

INTENTS = {
    "celebrate": ("celebrate", "congratulations", "happy", "开心", "庆祝", "成功"),
    "comfort": ("comfort", "hug", "安慰", "抱抱", "陪伴"),
    "like": ("like", "love", "喜欢", "喜爱", "爱心"),
    "surprised": ("surprised", "surprise", "惊讶", "意外"),
    "morning": ("morning", "早安", "早上好"),
    "sleep": ("sleep", "good night", "晚安", "睡觉"),
    "playful": ("playful", "joke", "调皮", "玩笑"),
}
MOCKERY = ("mock", "laugh at", "sarcas", "嘲笑", "嘲讽", "活该", "哈哈")


def key_hash(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValueError("MEME_KEY_INVALID")
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Metadata:
    category: str
    description: str
    tags: tuple[str, ...]
    suitable: tuple[str, ...]
    avoid: tuple[str, ...]
    source_page: str
    asset_url: str
    license: str
    usage_basis: str
    fetched_at: str
    revision: str = ""
    acquisition_json: str = ""

    def __post_init__(self):
        if not isinstance(self.category, str) or not CATEGORY.fullmatch(self.category):
            raise ValueError("MEME_CATEGORY_INVALID")
        for field, maximum in (
            (self.description, 2000), (self.source_page, 2048), (self.asset_url, 2048),
            (self.license, 256), (self.usage_basis, 512), (self.fetched_at, 64),
        ):
            if not isinstance(field, str) or not field.strip() or len(field) > maximum:
                raise ValueError("MEME_METADATA_INVALID")
        if not isinstance(self.revision, str) or len(self.revision) > 200:
            raise ValueError("MEME_METADATA_INVALID")
        if not isinstance(self.acquisition_json, str) or len(self.acquisition_json) > 32768:
            raise ValueError("MEME_METADATA_INVALID")
        if (not isinstance(self.tags, tuple) or len(self.tags) > 32
            or any(not isinstance(t, str) or not 1 <= len(t) <= 64 for t in self.tags)
            or any(not isinstance(v, tuple) or len(v) > len(INTENTS)
                   or any(t not in INTENTS for t in v) for v in (self.suitable, self.avoid))):
            raise ValueError("MEME_TAGS_INVALID")


def semantic_score(metadata, intent):
    """Bounded bilingual lexical semantics; no embedding/model quality claims."""
    text = (metadata.description + " " + " ".join(metadata.tags)).casefold()
    if intent in metadata.avoid or intent not in metadata.suitable:
        return 0
    if intent == "comfort" and any(word in text for word in MOCKERY):
        return 0
    return sum(word in text for word in INTENTS[intent])


class MemeCatalog:
    def __init__(self, db, library, *, clock=time.time, cooldown=1800,
                 max_assets=512, max_storage_bytes=64 * 1024 * 1024, max_bindings=10000):
        self.db, self.library, self.clock = db, library, clock
        if not 1 <= max_assets <= 4096 or not 1 <= max_bindings <= 100000:
            raise ValueError("MEME_CAPACITY_INVALID")
        if not 0 <= cooldown <= 86400 or not 1 <= max_storage_bytes <= 1024 ** 3:
            raise ValueError("MEME_CAPACITY_INVALID")
        self.cooldown, self.max_assets = cooldown, max_assets
        self.max_storage_bytes, self.max_bindings = max_storage_bytes, max_bindings
        # Each statement is safe inside a caller-owned transaction (no executescript commit).
        for sql in (
            "CREATE TABLE IF NOT EXISTS meme_assets (sha TEXT PRIMARY KEY, data BLOB, "
            "metadata TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS meme_offers (key TEXT PRIMARY KEY, intent TEXT NOT NULL, "
            "category TEXT, ids TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_pins (key TEXT PRIMARY KEY, sha TEXT, "
            "state TEXT NOT NULL, sent_at REAL)",
            "CREATE INDEX IF NOT EXISTS meme_pins_sha ON meme_pins(sha)",
        ):
            self.db.execute(sql)

    @contextmanager
    def atomic(self, check=None):
        # Serialize the check+write across independent SQLite workers as well as restarts.
        if self.db.in_transaction:
            raise RuntimeError("MEME_CALL_REQUIRES_TRANSACTION_BOUNDARY")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            if check:
                check()
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def add(self, data, metadata, *, check):
        image = self.library.validate_bytes(data)
        if image is None or not isinstance(metadata, Metadata):
            raise ValueError("MEME_ASSET_INVALID")
        with self.atomic(check):
            check()
            from .meme_semantic import stage_if_enabled

            row = self.db.execute("SELECT metadata FROM meme_assets WHERE sha=?",
                                  (image.sha256,)).fetchone()
            if row:
                stage_if_enabled(self, image.sha256, self.metadata(row[0]))
                return image.sha256  # Never reactivate a removed asset on rediscovery.
            count, size = self.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(LENGTH(data)),0) FROM meme_assets"
            ).fetchone()
            if count >= self.max_assets or size + len(data) > self.max_storage_bytes:
                raise ValueError("MEME_STORAGE_LIMIT")
            self.db.execute("INSERT INTO meme_assets(sha,data,metadata) VALUES (?,?,?)",
                            (image.sha256, data, json.dumps(asdict(metadata), ensure_ascii=False)))
            stage_if_enabled(self, image.sha256, metadata)
        return image.sha256

    def add_local(self, path, metadata, *, check):
        """Existing library path/symlink/byte checks also apply to local onboarding."""
        image = self.library._read(path)
        if image is None:
            raise ValueError("MEME_ASSET_INVALID")
        return self.add(image.data, metadata, check=check)

    @staticmethod
    def metadata(raw):
        value = json.loads(raw)
        for name in ("tags", "suitable", "avoid"):
            value[name] = tuple(value[name])
        return Metadata(**value)

    def _blocked(self, sha, key):
        return self.db.execute(
            "SELECT 1 FROM meme_pins WHERE sha=? AND key<>? AND "
            "(state IN ('PREPARED','SENDING','UNKNOWN') OR (state='SENT' AND sent_at>=?))",
            (sha, key, self.clock() - self.cooldown),
        ).fetchone() is not None

    def candidates(self, delivery_key, intent, *, category=None, limit=8, check):
        key = key_hash(delivery_key)
        if intent not in INTENTS or type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("MEME_INTENT_INVALID")
        if category is not None and (not isinstance(category, str)
                                     or not CATEGORY.fullmatch(category)):
            raise ValueError("MEME_CATEGORY_INVALID")
        with self.atomic(check):
            check()
            if not self.library.enabled or self.db.execute(
                "SELECT 1 FROM meme_pins WHERE key=? AND state<>'PREPARED'", (key,)
            ).fetchone():
                return []
            existing = self.db.execute(
                "SELECT intent,category,ids FROM meme_offers WHERE key=?", (key,)
            ).fetchone()
            if existing and (existing[0], existing[1]) != (intent, category):
                raise ValueError("MEME_OFFER_CONFLICT")
            rows = self.db.execute(
                "SELECT sha,metadata FROM meme_assets WHERE active=1 ORDER BY sha LIMIT ?",
                (self.max_assets,),
            ).fetchall()
            ranked = []
            from .meme_semantic import managed

            for sha, raw in rows:
                if managed(self.db, sha):
                    continue  # Managed assets require caption-based context retrieval.
                meta = self.metadata(raw)
                score = semantic_score(meta, intent)
                if score and (category is None or meta.category == category):
                    if not self._blocked(sha, key):
                        ranked.append((score, sha, meta))
            ranked.sort(key=lambda row: (-row[0], row[1]))
            if existing:
                ids = json.loads(existing[2])
                ranked = [row for row in ranked if row[1] in ids]
            else:
                if self.db.execute("SELECT COUNT(*) FROM meme_offers").fetchone()[0] >= (
                    self.max_bindings
                ):
                    raise ValueError("MEME_BINDING_LIMIT")
                ranked = ranked[:limit]
                self.db.execute("INSERT INTO meme_offers VALUES (?,?,?,?)",
                                (key, intent, category, json.dumps([r[1] for r in ranked])))
            return [{"candidate_id": sha, "description": meta.description,
                     "tags": list(meta.tags), "category": meta.category}
                    for _, sha, meta in ranked[:limit]]

    def prepare_selection(self, delivery_key, candidate_id, *, check):
        key = key_hash(delivery_key)
        if candidate_id is not None and (not isinstance(candidate_id, str)
                                        or not re.fullmatch(r"[0-9a-f]{64}", candidate_id)):
            raise ValueError("MEME_SELECTION_INVALID")
        with self.atomic(check):
            check()
            if not self.library.enabled:
                return None
            from .meme_semantic import usable

            if candidate_id is not None and not usable(self, candidate_id, key):
                return None
            pin = self.db.execute("SELECT sha,state FROM meme_pins WHERE key=?",
                                  (key,)).fetchone()
            if pin:
                if pin[0] != candidate_id:
                    raise ValueError("MEME_SELECTION_CONFLICT")
                if pin[1] != "PREPARED":
                    return None
            else:
                offer = self.db.execute("SELECT ids FROM meme_offers WHERE key=?",
                                        (key,)).fetchone()
                if not offer or (candidate_id is not None
                                 and candidate_id not in json.loads(offer[0])):
                    raise ValueError("MEME_NOT_OFFERED")
                if self.db.execute("SELECT COUNT(*) FROM meme_pins").fetchone()[0] >= (
                    self.max_bindings
                ):
                    raise ValueError("MEME_BINDING_LIMIT")
                blocked = candidate_id and self._blocked(candidate_id, key)
                self.db.execute("INSERT INTO meme_pins VALUES (?,?,?,NULL)",
                                (key, candidate_id,
                                 "PREPARED" if candidate_id and not blocked else "SKIPPED"))
                if blocked:
                    return None
            if candidate_id is None:
                return None
            row = self.db.execute("SELECT data FROM meme_assets WHERE sha=? AND active=1",
                                  (candidate_id,)).fetchone()
            image = self.library.validate_bytes(row[0]) if row else None
            if image is None or image.sha256 != candidate_id:
                return None
            return image

    def mark_sending(self, delivery_key, *, check):
        with self.atomic(check):
            check()
            if not self.library.enabled:
                raise ValueError("MEME_SEND_FENCED")
            from .meme_semantic import usable

            key = key_hash(delivery_key)
            pin = self.db.execute("SELECT sha FROM meme_pins WHERE key=?", (key,)).fetchone()
            if pin and pin[0] is not None and not usable(self, pin[0], key):
                raise ValueError("MEME_SEND_FENCED")
            cursor = self.db.execute(
                "UPDATE meme_pins SET state='SENDING' WHERE key=? AND state='PREPARED' "
                "AND sha IN (SELECT sha FROM meme_assets WHERE active=1)",
                (key_hash(delivery_key),),
            )
            if cursor.rowcount != 1:
                raise ValueError("MEME_SEND_FENCED")

    def receipt(self, delivery_key, outcome):
        if outcome not in {"SENT", "UNKNOWN", "NOT_SENT"}:
            raise ValueError("MEME_RECEIPT_INVALID")
        with self.atomic():
            key = key_hash(delivery_key)
            row = self.db.execute("SELECT state FROM meme_pins WHERE key=?", (key,)).fetchone()
            if row is None:
                raise ValueError("MEME_PIN_MISSING")
            target = "CANCELLED" if outcome == "NOT_SENT" else outcome
            if row[0] == target:
                return  # An ACK replay does not extend the success cooldown.
            allowed = {"PREPARED"} if outcome == "NOT_SENT" else {"SENDING", "UNKNOWN"}
            if row[0] not in allowed:
                raise ValueError("MEME_RECEIPT_CONFLICT")
            self.db.execute("UPDATE meme_pins SET state=?,sent_at=? WHERE key=?",
                            (target, self.clock() if outcome == "SENT" else None, key))

    def cancel(self, delivery_key):
        with self.atomic():
            key = key_hash(delivery_key)
            if self.db.execute("SELECT COUNT(*) FROM meme_pins").fetchone()[0] >= (
                self.max_bindings
            ) and not self.db.execute("SELECT 1 FROM meme_pins WHERE key=?", (key,)).fetchone():
                raise ValueError("MEME_BINDING_LIMIT")
            self.db.execute("INSERT OR IGNORE INTO meme_pins VALUES (?,NULL,'CANCELLED',NULL)",
                            (key,))
            self.db.execute("UPDATE meme_offers SET ids='[]' WHERE key=?", (key,))
            self.db.execute("UPDATE meme_pins SET state=CASE WHEN state='PREPARED' THEN "
                            "'CANCELLED' WHEN state='SENDING' THEN 'UNKNOWN' ELSE state END "
                            "WHERE key=?", (key,))

    def remove(self, sha):
        with self.atomic():
            from .meme_semantic import invalidate_in_transaction

            invalidate_in_transaction(self.db, sha)
            self.db.execute("UPDATE meme_assets SET active=0,data=NULL WHERE sha=?", (sha,))
