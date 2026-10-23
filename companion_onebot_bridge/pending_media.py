"""Durable original-media work after input admission; never replay uncertain I/O."""

import asyncio
import base64
import hashlib
import json
import re
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .files import MAX_FILE_BYTES, OriginalFile, fetch_file, original_files
from .images import OriginalImage, fetch_image, original_images, source_url
from .network import private_http_client
from .onebot_client import OneBotClient
from .spool import encode
from .voice_media import OriginalRecord, original_records
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
    from .voice_runtime import enabled

    shared = event.get("payload", {}).get("shared_content", [])
    images = (original_images(raw["message"]) if "payload" not in event
              or any(item.get("kind") == "image" for item in shared) else [])
    files = []
    if any(item.get("kind") == "file" for item in shared):
        if window.plan.value.get("live_text", {}).get("file_scope") != "owner_file_v1":
            raise ValueError("INVALID_FILE_SCOPE")
        files = original_files(raw["message"])
    records = list(original_records(raw["message"])) if enabled(window) else []
    if not images and not files and not records:
        return
    prepare(window)
    value = {"window_sha256": window.plan.sha256, "event_id": event["event_id"],
             "capture_sha256": digest(capture), "input_sha256": capture["input_sha256"],
             "images": [{key: item for key, item in asdict(image).items()
                         if key != "face_id" or item is not None} for image in images]}
    # Keep pre-file image manifests, signatures, counts and journal bytes exact.
    if files:
        value["files"] = [asdict(file) for file in files]
    if records:
        value.update(records=[asdict(record) for record in records],
                     source_event_id=event["source_event_id"])
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
        "image_count": len(images), **({"file_count": len(files)} if files else {}),
        **({"record_count": len(records)} if records else {})})
    with window.db:
        window.db.execute("INSERT INTO automatic_media VALUES(?,?,?,?,?,?,0,'PENDING',NULL)",
            (event["event_id"], window.plan.sha256, digest(capture), manifest_hash,
             encode(value), len(images)))
        window.db.execute("UPDATE test_window SET journal_head=?", (head,))


def manifest(window, row):
    value = json.loads(row["manifest_json"])
    try:
        fields = {"window_sha256", "event_id", "capture_sha256", "input_sha256",
                  "images", "signature"}
        if (set(value) not in (fields, fields | {"files"},
                              fields | {"records", "source_event_id"},
                              fields | {"files", "records", "source_event_id"})
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
        files = [OriginalFile(**item) for item in value.get("files", [])]
        records = [OriginalRecord(**item) for item in value.get("records", [])]
        if any(image.slot != slot or image.url != source_url(image)
               for slot, image in enumerate(images)):
            raise ValueError()
        if (not 1 <= len(images) + len(files) + len(records) <= 64
            or ("files" in value and (not files or window.plan.value.get(
                "live_text", {}).get("file_scope") != "owner_file_v1"))
            or any(file.slot != slot for slot, file in enumerate(files))
            or ("records" in value and (not records or window.plan.value.get(
                "live_text", {}).get("voice_scope") != "owner_voice_v1"
                or value["source_event_id"] != json.loads(source["capture_json"])[
                    "source_event_id"]))
            or any(record.slot != slot for slot, record in enumerate(records))
            or [record.index for record in records] != sorted({item.index for item in records})):
            raise ValueError()
    except (InvalidSignature, ValueError, TypeError, KeyError):
        raise ValueError("AUTOMATIC_MEDIA_CHANGED") from None
    return [*images, *files, *records]


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
                fields = {"event_hash", "manifest_sha256", "capture_sha256", "image_count"}
                if (set(data) not in (fields, fields | {"file_count"}, fields | {"record_count"},
                                     fields | {"file_count", "record_count"})
                    or type(data["image_count"]) is not int
                    or type(data.get("file_count", 0)) is not int
                    or type(data.get("record_count", 0)) is not int
                    or not 0 <= data["image_count"] <= 64
                    or not 0 <= data.get("file_count", 0) <= 64
                    or not 0 <= data.get("record_count", 0) <= 64
                    or not 1 <= (data["image_count"] + data.get("file_count", 0)
                                 + data.get("record_count", 0)) <= 64
                    or ("file_count" in data and data["file_count"] == 0)
                    or ("record_count" in data and data["record_count"] == 0)):
                    raise ValueError("AUTOMATIC_MEDIA_JOURNAL_CHANGED")
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
            or not 0 <= row["next_slot"] <= (row["image_count"] + expected.get("file_count", 0)
                                           + expected.get("record_count", 0))
            or (digest(json.loads(row["receipt_json"])) if row["receipt_json"] else None)
            != expected["receipt_sha256"]
            or (row["state"] in TERMINAL) != (row["manifest_json"] is None)
            or (row["manifest_json"] is not None and (
                digest(json.loads(row["manifest_json"])) != row["manifest_sha256"]
                or len(json.loads(row["manifest_json"]).get("files", []))
                != expected.get("file_count", 0)
                or len(json.loads(row["manifest_json"]).get("records", []))
                != expected.get("record_count", 0)))):
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


async def upload(window, client, event_id, images, *, files=(), records=(), start_slot=0,
                 check=None, stage=None,
                 image_transport=None, image_resolver=None, protocol=None,
                 file_transport=None, file_resolver=None, voice_transport=None, voice_resolver=None,
                 set_phase=lambda phase: None):
    """Shared one-shot transfer, with every external boundary revalidating the source."""
    check = check or (lambda: window.verify())

    async def guard():
        value = check()
        if hasattr(value, "__await__"):
            await value

    async def progress(state, slot, receipt=None):
        if stage:
            stage(state, slot, receipt)

    if (start_slot < len(images)
        and window.plan.value["live_text"].get("companion_scope") == "owner_private_v1"):
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
        await progress("DONE" if image.slot + 1 == len(images) + len(files) + len(records)
                       else "PENDING",
                       image.slot + 1, result)
    for original in files[max(0, start_slot - len(images)):]:
        position = len(images) + original.slot
        await guard()
        await progress("CLAIMING", position)
        prefix = "/internal/v2/content/" + event_id + "/file"
        claim = await window.core_post(client, prefix + "/claim", {
            "request_key": f"file:{original.slot}", "source_sha256": original.source_sha256,
        })
        _file_claim(claim, window)
        await progress("DOWNLOADING", position, claim)

        async def check_file(*, original=original, claim=claim, prefix=prefix):
            await guard()
            receipt = await window.core_post(client, prefix + "/check", {
                "lease_token": claim["lease_token"], "source_sha256": original.source_sha256,
            })
            if (receipt != {"approved": True, "lease_until": claim["lease_until"]}
                or type(receipt.get("approved")) is not bool):
                raise ValueError("FILE_DOWNLOAD_CHECK_REJECTED")
            await guard()
            if datetime.fromisoformat(claim["lease_until"]) <= window.clock():
                raise ValueError("FILE_DOWNLOAD_LEASE_EXPIRED")

        options = {"resolver": file_resolver} if file_resolver is not None else {}
        set_phase("file_download")
        async with (nullcontext(protocol) if protocol is not None else private_http_client(
            window.plan.config.onebot_http_url, window.plan.config.onebot_token,
        )) as raw_protocol:
            async with window.file_http(raw_protocol, original, check_file) as scoped:
                downloaded = await fetch_file(
                    original, claim["max_bytes"],
                    onebot=OneBotClient(window.plan.config, scoped), check=check_file,
                    transport=file_transport,
                    synthetic=window.plan.value["execution"] == "local_synthetic", **options,
                )
        await check_file()
        proof = {"plan_sha256": window.plan.sha256, "event_id": event_id,
                 "slot": original.slot, "source_sha256": original.source_sha256,
                 "lease_token": claim["lease_token"], "file_id": original.file_id,
                 "filename": original.filename, "media_sha256": downloaded.media_sha256,
                 "mime": downloaded.mime, "bytes": len(downloaded.body)}
        proof["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
            window.plan.config.capture_signing_key)).sign(
                b"companion-core/onebot-owner-file/v1\x00" + encode(proof).encode()).hex()
        proof_header = base64.urlsafe_b64encode(encode(proof).encode()).decode("ascii")
        if len(proof_header) > 16384:
            raise ValueError("FILE_UPLOAD_PROOF_TOO_LARGE")
        set_phase("file_upload")
        await progress("UPLOADING", position)
        async with window.core_http(client) as scoped:
            async with scoped.stream("POST", prefix, content=downloaded.body, timeout=60,
                headers={"Content-Type": downloaded.mime,
                         "X-Content-Key": f"file:{original.slot}",
                         "X-Content-Lease": claim["lease_token"],
                         "X-Content-Proof": proof_header}) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ValueError("INVALID_CORE_RECEIPT")
            from .event_normalizer import decode

            result = decode(bytes(body), 65536)
        if (set(result) != {"item_id", "state", "duplicate"}
            or not isinstance(result["item_id"], str)
            or not re.fullmatch(r"content_[0-9a-f]{32}", result["item_id"])
            or type(result["duplicate"]) is not bool
            or result["state"] not in {"QUEUED", "RUNNING", "SUCCEEDED"}
            or (not result["duplicate"] and result["state"] != "QUEUED")):
            raise ValueError("FILE_UPLOAD_RECEIPT_REJECTED")
        await guard()
        await progress("DONE" if position + 1 == len(images) + len(files) + len(records)
                       else "PENDING",
                       position + 1, result)
    for original in records[max(0, start_slot - len(images) - len(files)):]:
        from .voice_runtime import upload as upload_voice

        position = len(images) + len(files) + original.slot
        result = await upload_voice(window, client, event_id, original, check=guard,
            progress=lambda state, receipt, position=position: progress(state, position, receipt),
            protocol=protocol, voice_transport=voice_transport, voice_resolver=voice_resolver,
            set_phase=set_phase)
        await progress("DONE" if position + 1 == len(images) + len(files) + len(records)
                       else "PENDING", position + 1, result)


def _file_claim(value, window):
    try:
        if (not isinstance(value, dict)
            or set(value) != {"state", "lease_token", "lease_until", "max_bytes"}
            or value["state"] != "FETCHING"
            or not isinstance(value["lease_token"], str)
            or not re.fullmatch(r"content_lease_[0-9a-f]{32}", value["lease_token"])
            or type(value["max_bytes"]) is not int
            or not 1 <= value["max_bytes"] <= MAX_FILE_BYTES
            or not isinstance(value["lease_until"], str)):
            raise ValueError()
        until = datetime.fromisoformat(value["lease_until"])
        if not until.tzinfo or not window.clock() < until:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("FILE_DOWNLOAD_CLAIM_REJECTED") from None


class MediaPump:
    def __init__(self, window, client, *, image_transport=None, image_resolver=None,
                 protocol=None, file_transport=None, file_resolver=None,
                 voice_transport=None, voice_resolver=None,
                 set_phase=lambda phase: None):
        self.window, self.client, self.task = window, client, None
        self.image_options = {"image_transport": image_transport, "image_resolver": image_resolver}
        self.file_options = {"protocol": protocol, "file_transport": file_transport,
                             "file_resolver": file_resolver, "voice_transport": voice_transport,
                             "voice_resolver": voice_resolver, "set_phase": set_phase}

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
            media = manifest(self.window, row)
            images = [item for item in media if isinstance(item, OriginalImage)]
            files = [item for item in media if isinstance(item, OriginalFile)]
            records = [item for item in media if isinstance(item, OriginalRecord)]
            if row["state"] != "PENDING":
                if not 0 <= row["next_slot"] < len(media):
                    raise ValueError("AUTOMATIC_MEDIA_RECONCILIATION_REQUIRED")
                original = media[row["next_slot"]]
                if isinstance(original, OriginalRecord):
                    from .voice_runtime import recover

                    receipt = await recover(self.window, self.client, row, original,
                                            lambda: self.check_source(row))
                    next_slot = row["next_slot"] + 1
                    transition(self.window, row["event_id"],
                        "DONE" if next_slot == len(media) else "PENDING", next_slot, receipt)
                    return
                kind = "qq_file" if isinstance(original, OriginalFile) else "qq_image"
                async with asyncio.timeout(300):
                    while True:
                        await self.check_source(row)
                        status = await self.window.core_get(self.client,
                            "/internal/v2/content/" + row["event_id"])
                        items = {item["slot"]: item["state"] for item in status.get("items", [])
                                 if item.get("kind") == kind}
                        state = items.get(original.slot)
                        pending = {"FETCHING"} if kind == "qq_file" else {"FETCHING", "RUNNING"}
                        if row["state"] != "UPLOADING" or state not in pending:
                            break
                        until = datetime.fromisoformat(status["wait_until"])
                        if until <= self.window.clock():
                            raise ValueError("AUTOMATIC_MEDIA_RECONCILIATION_REQUIRED")
                        await asyncio.sleep(0.25)
                accepted = ({"QUEUED", "RUNNING", "SUCCEEDED"} if kind == "qq_file"
                            else {"STAGED", "SUCCEEDED"})
                if row["state"] == "UPLOADING" and state in accepted:
                    next_slot = row["next_slot"] + 1
                    transition(self.window, row["event_id"],
                        "DONE" if next_slot == len(media) else "PENDING", next_slot, status)
                    return
                if row["state"] == "CLAIMING" and state == "WAITING":
                    transition(self.window, row["event_id"], "PENDING", row["next_slot"], status)
                    return
                raise ValueError("AUTOMATIC_MEDIA_RECONCILIATION_REQUIRED")
            await upload(self.window, self.client, row["event_id"], images, files=files,
                records=records,
                start_slot=row["next_slot"], check=lambda: self.check_source(row),
                stage=lambda state, slot, receipt: transition(
                    self.window, row["event_id"], state, slot, receipt),
                **self.image_options, **self.file_options)
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
