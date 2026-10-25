"""Original immutable pin/receipt custody, independent of candidate business storage."""

import hashlib
import re
import time
from contextlib import contextmanager

from . import journal
from .reply_segments import event_id as delivery_event_id
from .spool import encode


def key_hash(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValueError("MEME_KEY_INVALID")
    return hashlib.sha256(value.encode()).hexdigest()


class PinCustody:
    def __init__(self, db, *, clock=time.time, max_bindings=10000, cooldown=1800):
        self.db, self.clock, self.max_bindings, self.cooldown = db, clock, max_bindings, cooldown
        db.execute(
            "CREATE TABLE IF NOT EXISTS meme_pins (key TEXT PRIMARY KEY, sha TEXT, "
            "state TEXT NOT NULL, sent_at REAL)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS meme_pins_sha ON meme_pins(sha)")

    @contextmanager
    def atomic(self, check=None):
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

    def blocked(self, sha, key):
        return (
            self.db.execute(
                "SELECT 1 FROM meme_pins WHERE sha=? AND key<>? AND "
                "(state IN ('PREPARED','SENDING','UNKNOWN')"
                + (" OR (state='SENT' AND sent_at>=?)" if self.cooldown is not None else "")
                + ")",
                (sha, key, *((self.clock() - self.cooldown,) if self.cooldown is not None else ())),
            ).fetchone()
            is not None
        )

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
                return
            allowed = {"PREPARED"} if outcome == "NOT_SENT" else {"SENDING", "UNKNOWN"}
            if row[0] not in allowed:
                raise ValueError("MEME_RECEIPT_CONFLICT")
            self.db.execute(
                "UPDATE meme_pins SET state=?,sent_at=? WHERE key=?",
                (target, self.clock() if outcome == "SENT" else None, key),
            )

    def cancel_in_transaction(self, key):
        if not self.db.in_transaction:
            raise RuntimeError("MEME_TRANSACTION_REQUIRED")
        if (
            self.db.execute("SELECT COUNT(*) FROM meme_pins").fetchone()[0] >= self.max_bindings
            and not self.db.execute("SELECT 1 FROM meme_pins WHERE key=?", (key,)).fetchone()
        ):
            raise ValueError("MEME_BINDING_LIMIT")
        self.db.execute("INSERT OR IGNORE INTO meme_pins VALUES (?,NULL,'CANCELLED',NULL)", (key,))
        self.db.execute(
            "UPDATE meme_pins SET state=CASE WHEN state='PREPARED' THEN "
            "'CANCELLED' WHEN state='SENDING' THEN 'UNKNOWN' ELSE state END WHERE key=?",
            (key,),
        )


class DeliveryCustody:
    """The unchanged delivery identity and original NOT_SENT proof, shared by both modes."""

    def _item(self, item):
        image = item.get("content", {}).get("image")
        event_id = delivery_event_id(item)
        if (
            not isinstance(image, dict)
            or set(image) != {"source", "offer_key", "candidate_id", "category"}
            or image["source"] != "companion.meme_catalog"
            or image["offer_key"] != "meme:" + event_id
            or not isinstance(image["candidate_id"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", image["candidate_id"])
            or not isinstance(image["category"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", image["category"])
            or not isinstance(item.get("delivery_key"), str)
            or item.get("authority") != self.authority
        ):
            raise ValueError("MEME_DELIVERY_BINDING_INVALID")
        key_hash(item["delivery_key"])
        return event_id, image

    def _binding(self, item, *, create=False):
        event_id, image = self._item(item)
        expected = (
            item["delivery_key"],
            image["offer_key"],
            event_id,
            image["candidate_id"],
            image["category"],
            self.authority_sha256,
        )
        row = self.db.execute(
            "SELECT * FROM meme_runtime_bindings WHERE delivery_key=? OR offer_key=?", expected[:2]
        ).fetchone()
        if row is not None and tuple(row) != expected:
            raise ValueError("MEME_DELIVERY_BINDING_CONFLICT")
        if row is None:
            if not create or not self.db.in_transaction:
                raise ValueError("MEME_DELIVERY_BINDING_MISSING")
            self.db.execute("INSERT INTO meme_runtime_bindings VALUES (?,?,?,?,?,?)", expected)
        return event_id, image

    def receipt(self, item, outcome):
        _, image = self._binding(item)
        self.pins.receipt(image["offer_key"], outcome)

    def confirm_not_sent(self, item, operation, core_receipt):
        self.window.verify(active=False)
        attempt = self.db.execute(
            "SELECT state,item_hash,plan_hash,event_id FROM window_attempts WHERE message_id=?",
            (item["message_id"],),
        ).fetchone()
        expected = {
            "cancelled": True,
            "message_id": item["message_id"],
            "state": "CANCELLED",
            "send_attempt_retained": True,
        }
        if (
            getattr(operation, "runtime", None) is not self.window
            or getattr(operation, "current_item", None) is not item
            or getattr(operation, "dispatched", None) is not False
            or getattr(operation, "authority", None) != self.authority
            or getattr(operation, "item_hash", None) != journal.digest(item)
            or attempt is None
            or tuple(attempt)
            != ("NOT_SENT", journal.digest(item), self.authority_sha256, delivery_event_id(item))
            or encode(core_receipt) != encode(expected)
        ):
            raise ValueError("MEME_NOT_SENT_PROOF_REQUIRED")
        with self.pins.atomic():
            _, image = self._binding(item)
            key = key_hash(image["offer_key"])
            pin = self.db.execute("SELECT sha,state FROM meme_pins WHERE key=?", (key,)).fetchone()
            if pin is None or pin[0] != image["candidate_id"]:
                raise ValueError("MEME_DELIVERY_BINDING_CONFLICT")
            if pin[1] not in {"PREPARED", "SENDING", "CANCELLED"}:
                raise ValueError("MEME_RECEIPT_CONFLICT")
            self.db.execute("UPDATE meme_pins SET state='CANCELLED' WHERE key=?", (key,))


class CoreMemeTransport(DeliveryCustody):
    def __init__(self, window):
        self.window, self.spool, self.db = window, window.spool, window.db
        if self.db.in_transaction:
            raise RuntimeError("MEME_RUNTIME_REQUIRES_TRANSACTION_BOUNDARY")
        self.config, self.authority = window.config, dict(window.authority)
        self.authority_sha256 = journal.digest(self.authority)
        self.pins = PinCustody(
            self.db,
            max_bindings=self.config.meme_catalog_max_bindings,
            clock=lambda: window.clock().timestamp(),
            cooldown=None,
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS meme_runtime_bindings (delivery_key TEXT PRIMARY KEY,"
            "offer_key TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL,candidate_id TEXT NOT NULL,"
            "category TEXT NOT NULL,window_sha256 TEXT NOT NULL)"
        )
        self.db.commit()

    def _event_check(self, event_id):
        from . import owner_notices

        self.window.verify()
        if self.window.authority != self.authority or not self.config.meme_enabled:
            raise ValueError("MEME_SEND_FENCED")
        row = self.db.execute(
            "SELECT rowid,state,plan_hash FROM window_inputs WHERE event_id=?", (event_id,)
        ).fetchone()
        if (
            not row
            or row[2] != self.authority_sha256
            or row[1] in {"CANCELLED", "SUPERSEDED", "RECALLED"}
            or self.db.execute(
                "SELECT 1 FROM window_inputs WHERE plan_hash=? AND rowid>? LIMIT 1",
                (self.authority_sha256, row[0]),
            ).fetchone()
            or owner_notices.recalled(self.window, event_id)
        ):
            raise ValueError("MEME_INPUT_CANCELLED")

    def prepare_offer(self, event, capture=None):
        return None  # Native ingress never contributes a business sidecar in Core mode.

    async def prepare(self, client, item, *, check):
        from .meme_asset_http import read_asset

        event_id, image = self._item(item)
        original = journal.digest(item)

        def fence():
            check()
            self._event_check(event_id)
            if journal.digest(item) != original:
                raise ValueError("MEME_DELIVERY_BINDING_CONFLICT")

        prepared = await read_asset(self.window, client, item, check=fence)
        fence()
        self.spool.capacity(4096)
        with self.pins.atomic(fence):
            fence()
            self._binding(item, create=True)
            key = key_hash(image["offer_key"])
            pin = self.db.execute("SELECT sha,state FROM meme_pins WHERE key=?", (key,)).fetchone()
            if pin and tuple(pin) != (image["candidate_id"], "PREPARED"):
                raise ValueError("MEME_SEND_FENCED")
            if not pin:
                if (
                    self.db.execute("SELECT COUNT(*) FROM meme_pins").fetchone()[0]
                    >= self.pins.max_bindings
                ):
                    raise ValueError("MEME_BINDING_LIMIT")
                if self.pins.blocked(image["candidate_id"], key):
                    raise ValueError("MEME_SEND_FENCED")
                self.db.execute(
                    "INSERT INTO meme_pins VALUES (?,?,'PREPARED',NULL)",
                    (key, image["candidate_id"]),
                )
        return prepared

    def mark_sending(self, item, *, check):
        event_id, image = self._binding(item)

        def fence():
            check()
            self._event_check(event_id)
            self._binding(item)

        with self.pins.atomic(fence):
            fence()
            if self.pins.blocked(image["candidate_id"], key_hash(image["offer_key"])):
                raise ValueError("MEME_SEND_FENCED")
            cursor = self.db.execute(
                "UPDATE meme_pins SET state='SENDING' WHERE key=? AND sha=? AND state='PREPARED'",
                (key_hash(image["offer_key"]), image["candidate_id"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("MEME_SEND_FENCED")

    def cancel(self, event_or_offer):
        key = event_or_offer if event_or_offer.startswith("meme:") else "meme:" + event_or_offer
        if not re.fullmatch(r"evt_[a-f0-9]{64}", key.removeprefix("meme:")):
            raise ValueError("MEME_INPUT_INVALID")
        with self.pins.atomic():
            self.pins.cancel_in_transaction(key_hash(key))
