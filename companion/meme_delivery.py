"""Delivery-scoped original byte reads under the existing native Outbox lease."""

import base64
import json

from pydantic import Field

from companion import owner_delivery, reply_revision
from companion.contracts import CoreError
from companion.meme_core import MemeCaptionService
from companion.meme_retrieval import SHA, AssetBinding, MemeRetrieval, core_owned
from companion.owner_authority_protocol import AuthorityReference
from companion_meme_protocol import digest


class AssetRequest(AuthorityReference):
    event_id: str = Field(pattern=r"^evt_[a-f0-9]{64}$")
    reply_revision: int = Field(strict=True, ge=1, lt=2**53)
    lease_token: str = Field(pattern=r"^lease_[a-f0-9]{32}$")
    content_sha256: SHA


def read_asset(store, settings, clock, message_id, body):
    if not core_owned(settings):
        raise CoreError("MEME_CORE_MODE_REQUIRED", 403)
    with store.transaction():
        permission = owner_delivery.check_delivery(store, settings, clock, message_id)
        row = store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
        if (
            row["state"] not in {"LEASED", "SENDING"}
            or not row["lease_until"]
            or row["lease_until"] <= clock()
            or body.lease_token != row["lease_token"]
            or body.authority != json.loads(row["authority_json"])
            or body.event_id != row["origin_event_id"]
            or body.content_sha256 != row["content_sha256"]
        ):
            raise CoreError("OWNER_DELIVERY_LEASE_LOST", 409)
        reply_revision.require_reply_source(store, body.event_id, body.reply_revision)
        saved = store.one(
            "SELECT value FROM runtime_meta WHERE key=?", (owner_delivery.DELIVERY + message_id,)
        )
        material = json.loads(saved["value"])
        job = store.one("SELECT * FROM jobs WHERE job_id=?", (material["job_id"],))
        retrieval = MemeRetrieval(MemeCaptionService(store, settings, None, clock))
        raw = retrieval.selected(job, json.loads(row["content_json"]).get("image") or {})
        binding = AssetBinding.model_validate(raw)
        _, _, _, _, image = retrieval._resolve(binding.asset_id, binding.caption_job_id)
        return {
            "message_id": message_id,
            "delivery_key": row["delivery_key"],
            "event_id": body.event_id,
            "reply_revision": body.reply_revision,
            "authority": body.authority,
            "content_sha256": body.content_sha256,
            "lease_until": permission["lease_until"],
            **{
                key: raw[key]
                for key in (
                    "asset_id",
                    "asset_sha256",
                    "source_sha256",
                    "custody_sha256",
                    "record_sha256",
                    "caption_sha256",
                )
            },
            "binding_sha256": digest(raw),
            "mime": image.mime_type,
            "byte_length": len(image.data),
            "data_base64": base64.b64encode(image.data).decode(),
        }
