"""Durable original-image work after input admission; never replay uncertain I/O."""

import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import datetime

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .images import OriginalImage, fetch_image, original_images, source_url
from .spool import encode
from .window import digest

DOMAIN = b"companion-core/onebot-owner-media-queue/v1\x00"
TERMINAL = {"DONE", "CANCELLED"}


def exists(window):
    return bool(window.db.execute("SELECT 1 FROM sqlite_master WHERE name='automatic_media'")
                .fetchone())


def prepare(window):
    window.db.execute("CREATE TABLE IF NOT EXISTS automatic_media(event_id TEXT PRIMARY KEY,"
                      "plan_hash TEXT NOT NULL,capture_sha256 TEXT NOT NULL,"
                      "manifest_sha256 TEXT NOT NULL,manifest_json TEXT,"
                      "image_count INTEGER NOT NULL,"
                      "next_slot INTEGER NOT NULL,state TEXT NOT NULL,receipt_json TEXT)")
    window.db.commit()


def queue(window, event, raw, capture):
    images = original_images(raw["message"])
    if not images:
        return
    prepare(window)
    value = {"window_sha256": window.plan.sha256, "event_id": event["event_id"],
             "capture_sha256": digest(capture), "input_sha256": capture["input_sha256"],
             "images": [asdict(image) for image in images]}
    value["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
        window.plan.config.capture_signing_key)).sign(DOMAIN + encode(value).encode()).hex()
    prior = window.db.execute("SELECT * FROM automatic_media WHERE event_id=?",
                              (event["event_id"],)).fetchone()
    manifest_hash = digest(value)
    if prior:
        if prior["manifest_sha256"] != manifest_hash:
            raise ValueError("AUTOMATIC_MEDIA_CHANGED")
        return
    window.spool.capacity(len(encode(value).encode()) * 3)
    head = window.append("MEDIA_QUEUED", {"event_hash": digest(event["event_id"]),
        "manifest_sha256": manifest_hash, "capture_sha256": digest(capture),
        "image_count": len(images)})
    with window.db:
        window.db.execute("INSERT INTO automatic_media VALUES(?,?,?,?,?,?,0,'PENDING',NULL)",
            (event["event_id"], window.plan.sha256, digest(capture), manifest_hash,
             encode(value), len(images)))
        window.db.execute("UPDATE test_window SET journal_head=?", (head,))


def manifest(window, row):
    value = json.loads(row["manifest_json"])
    try:
        if (set(value) != {"window_sha256", "event_id", "capture_sha256", "input_sha256",
                           "images", "signature"}
            or digest(value) != row["manifest_sha256"]):
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(
            window.plan.config.capture_public_key)).verify(bytes.fromhex(value["signature"]),
                DOMAIN + encode({key: item for key, item in value.items()
                                 if key != "signature"}).encode())
        source = window.db.execute("SELECT * FROM window_inputs WHERE event_id=?",
                                    (row["event_id"],)).fetchone()
        if (not source or value["event_id"] != row["event_id"]
            or value["window_sha256"] != row["plan_hash"]
            or row["plan_hash"] != window.plan.sha256
            or value["capture_sha256"] != row["capture_sha256"]
            or value["capture_sha256"] != digest(json.loads(source["capture_json"]))
            or value["input_sha256"] != source["input_hash"]
            or len(value["images"]) != row["image_count"]):
            raise ValueError()
        images = [OriginalImage(**item) for item in value["images"]]
        if any(image.slot != slot or image.url != source_url(image)
               for slot, image in enumerate(images)):
            raise ValueError()
    except (InvalidSignature, ValueError, TypeError, KeyError):
        raise ValueError("AUTOMATIC_MEDIA_CHANGED") from None
    return images


def transition(window, event_id, state, next_slot, receipt=None):
    row = window.db.execute("SELECT * FROM automatic_media WHERE event_id=?",
                            (event_id,)).fetchone()
    if row["state"] in TERMINAL:
        return
    head = window.append("MEDIA_STATE", {"event_hash": digest(event_id), "state": state,
        "next_slot": next_slot, "receipt_sha256": digest(receipt) if receipt is not None else None})
    with window.db:
        window.db.execute("UPDATE automatic_media SET state=?,next_slot=?,receipt_json=?,"
                          "manifest_json=CASE WHEN ? THEN NULL ELSE manifest_json END "
                          "WHERE event_id=?",
                          (state, next_slot, encode(receipt) if receipt else None,
                           state in TERMINAL, event_id))
        window.db.execute("UPDATE test_window SET journal_head=?", (head,))
    if state in TERMINAL:
        window.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def verify_history(window, history):
    projected = {}
    for plan, entries in history:
        for entry in entries:
            if entry["kind"] == "MEDIA_QUEUED":
                data = entry["data"]
                key = data["event_hash"]
                if key in projected:
                    raise ValueError("AUTOMATIC_MEDIA_JOURNAL_CHANGED")
                projected[key] = {**data, "plan_hash": plan.sha256, "state": "PENDING",
                                  "next_slot": 0, "receipt_sha256": None}
            elif entry["kind"] == "MEDIA_STATE":
                data = entry["data"]
                prior = projected.get(data["event_hash"])
                if not prior or prior["state"] in TERMINAL or prior["plan_hash"] != plan.sha256:
                    raise ValueError("AUTOMATIC_MEDIA_JOURNAL_CHANGED")
                prior.update(data)
    rows = window.db.execute("SELECT * FROM automatic_media").fetchall() if exists(window) else []
    if set(projected) != {digest(row["event_id"]) for row in rows}:
        raise ValueError("AUTOMATIC_MEDIA_JOURNAL_CHANGED")
    for row in rows:
        expected = projected[digest(row["event_id"])]
        if (any(row[key] != expected[key] for key in (
            "plan_hash", "manifest_sha256", "capture_sha256", "image_count", "state", "next_slot"))
            or not 0 <= row["next_slot"] <= row["image_count"]
            or (digest(json.loads(row["receipt_json"])) if row["receipt_json"] else None)
            != expected["receipt_sha256"]
            or (row["state"] in TERMINAL) != (row["manifest_json"] is None)
            or (row["manifest_json"] is not None
                and digest(json.loads(row["manifest_json"])) != row["manifest_sha256"])):
            raise ValueError("AUTOMATIC_MEDIA_JOURNAL_CHANGED")


def cancel(window, event_id, receipt):
    if not exists(window):
        return
    row = window.db.execute("SELECT * FROM automatic_media WHERE event_id=?",
                            (event_id,)).fetchone()
    if row and row["state"] not in TERMINAL:
        transition(window, event_id, "CANCELLED", row["next_slot"], receipt)


def stop(window):
    if exists(window):
        for row in window.db.execute("SELECT event_id FROM automatic_media WHERE plan_hash=? "
                                      "AND state NOT IN ('DONE','CANCELLED')",
                                      (window.plan.sha256,)).fetchall():
            cancel(window, row["event_id"], {"window_sha256": window.plan.sha256,
                                           "reason": "WINDOW_STOPPED"})


async def upload(window, client, event_id, images, *, start_slot=0, check=None, stage=None,
                 image_transport=None, image_resolver=None, set_phase=lambda phase: None):
    """Shared one-shot transfer, with every external boundary revalidating the source."""
    check = check or (lambda: window.verify())

    async def guard():
        value = check()
        if hasattr(value, "__await__"):
            await value

    async def progress(state, slot, receipt=None):
        if stage:
            stage(state, slot, receipt)

    if window.plan.value["live_text"].get("companion_scope") == "owner_private_v1":
        from .continuous import core_status

        async with asyncio.timeout(60):
            while (await core_status(window, client))["operations"]["budget"]["unsettled_calls"]:
                await guard()
                await asyncio.sleep(0.25)
    prefix = "/internal/v2/content/" + event_id + "/image"
    for image in images[start_slot:]:
        await guard()
        await progress("CLAIMING", image.slot)
        claim = await window.core_post(client, prefix + "/claim",
            {"request_key": f"image:{image.slot}", "source_sha256": image.source_sha256})
        await progress("DOWNLOADING", image.slot, claim)

        async def check_image(*, image=image, claim=claim):
            await guard()
            await window.core_post(client, prefix + "/check", {
                "lease_token": claim["lease_token"], "source_sha256": image.source_sha256})
            await guard()
            if datetime.fromisoformat(claim["lease_until"]) <= window.clock():
                raise ValueError("IMAGE_DOWNLOAD_LEASE_EXPIRED")

        options = {"resolver": image_resolver} if image_resolver is not None else {}
        set_phase("image_download")
        data, mime = await fetch_image(image, claim["max_bytes"], check=check_image,
                                       transport=image_transport, **options)
        await check_image()
        proof = {"plan_sha256": window.plan.sha256, "event_id": event_id,
                 "source_sha256": image.source_sha256, "lease_token": claim["lease_token"],
                 "media_sha256": hashlib.sha256(data).hexdigest(), "mime": mime}
        proof["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
            window.plan.config.capture_signing_key)).sign(
                b"companion-core/onebot-owner-image/v1\x00" + encode(proof).encode()).hex()
        set_phase("image_upload")
        await progress("UPLOADING", image.slot)
        async with window.core_http(client) as scoped:
            response = await scoped.post(prefix, content=data,
                # Native analysis may include a primary-model decision and a bounded
                # video call. Core retains the source/window and absolute deadline fences.
                timeout=300, headers={"Content-Type": mime, "X-Content-Key": f"image:{image.slot}",
                    "X-Content-Lease": claim["lease_token"], "X-Content-Proof": encode(proof)})
            response.raise_for_status()
            result = response.json()
            expected = "SUCCEEDED" if image.slot == len(images) - 1 else "STAGED"
            if result.get("state") != expected:
                raise ValueError("IMAGE_ANALYSIS_FAILED_CLOSED")
        await guard()
        await progress("DONE" if image.slot == len(images) - 1 else "PENDING",
                       image.slot + 1, result)


class MediaPump:
    def __init__(self, window, client, *, image_transport=None, image_resolver=None):
        self.window, self.client, self.task = window, client, None
        self.image_options = {"image_transport": image_transport, "image_resolver": image_resolver}

    async def check_source(self, row):
        from .continuous import require_scope

        require_scope(self.window)
        source = self.window.db.execute("SELECT * FROM window_inputs WHERE event_id=?",
                                        (row["event_id"],)).fetchone()
        state = await self.window.input_state(self.client, source)
        if state["state"] in {"SUPERSEDED", "RECALLED"}:
            cancel(self.window, row["event_id"], state)
            raise SourceCancelled()
        return state

    async def execute(self, row):
        try:
            await self.check_source(row)
            images = manifest(self.window, row)
            if row["state"] != "PENDING":
                async with asyncio.timeout(300):
                    while True:
                        await self.check_source(row)
                        status = await self.window.core_get(self.client,
                            "/internal/v2/content/" + row["event_id"])
                        items = {item["slot"]: item["state"] for item in status.get("items", [])
                                 if item.get("kind") == "qq_image"}
                        state = items.get(row["next_slot"])
                        if row["state"] != "UPLOADING" or state not in {"FETCHING", "RUNNING"}:
                            break
                        until = datetime.fromisoformat(status["wait_until"])
                        if until <= self.window.clock():
                            raise ValueError("AUTOMATIC_MEDIA_RECONCILIATION_REQUIRED")
                        await asyncio.sleep(0.25)
                if row["state"] == "UPLOADING" and state in {"STAGED", "SUCCEEDED"}:
                    next_slot = row["next_slot"] + 1
                    transition(self.window, row["event_id"],
                        "DONE" if next_slot == len(images) else "PENDING", next_slot, status)
                    return
                if row["state"] == "CLAIMING" and state == "WAITING":
                    transition(self.window, row["event_id"], "PENDING", row["next_slot"], status)
                    return
                raise ValueError("AUTOMATIC_MEDIA_RECONCILIATION_REQUIRED")
            await upload(self.window, self.client, row["event_id"], images,
                start_slot=row["next_slot"], check=lambda: self.check_source(row),
                stage=lambda state, slot, receipt: transition(
                    self.window, row["event_id"], state, slot, receipt), **self.image_options)
        except SourceCancelled:
            return
        except (httpx.HTTPError, ValueError):
            # A failed source check proves cancellation, not a successful upload.
            # Other errors retain their exact stage and remain fail-closed.
            try:
                await self.check_source(row)
            except SourceCancelled:
                return
            raise

    def tick(self):
        if self.task is not None:
            if not self.task.done():
                return
            self.task.result()
            self.task = None
        if exists(self.window):
            row = self.window.db.execute("SELECT * FROM automatic_media WHERE plan_hash=? "
                "AND state NOT IN ('DONE','CANCELLED') ORDER BY rowid LIMIT 1",
                (self.window.plan.sha256,)).fetchone()
            if row:
                self.task = asyncio.create_task(self.execute(row))

    def pending(self, event_id):
        return exists(self.window) and bool(self.window.db.execute(
            "SELECT 1 FROM automatic_media WHERE event_id=? AND state NOT IN ('DONE','CANCELLED')",
            (event_id,),
        ).fetchone())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class SourceCancelled(Exception):
    pass
