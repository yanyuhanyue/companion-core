"""Explicit renewal in the original carry journal; no settlement or input replay."""

import json
from datetime import datetime

from companion import model_carry, owner_authority, paid_budget
from companion.contracts import CoreError
from companion_delivery_protocol import digest, hash_value


def manifest(store, settings, authority, window, witness, view, original, stop, work):
    prior = view["model_carries"].get(original.policy.unknown_carry_sha256)
    if prior is None:
        raise CoreError("OWNER_MODEL_CARRY_REQUIRED", 403)
    model_carry._continuation(
        store,
        settings,
        authority,
        prior,
        view,
        approved_contract_sha256=original.policy.paid_contract_sha256,
    )
    # This narrow renewal only carries already-approved historical liability.
    # A new uncertain input needs its own original execution-closure review.
    unknown = sorted(k for k, v in view["model_latest"].items() if v["state"] == "UNKNOWN")
    if unknown != [item["request_id"] for item in prior["manifest"]["unknowns"]] or any(
        not state["terminal"] for state in work.values()
    ):
        raise CoreError("OWNER_MODEL_RENEWAL_CURRENT_WORK_REQUIRED", 409)
    windows = sorted(
        set(prior["manifest"]["budget_scope_window_ids"])
        | {prior["manifest"]["successor_window_id"]}
    )
    return {
        "authority": authority,
        "core_stop_sha256": stop["core_stop_sha256"],
        "budget_journal_id": original.journal_id,
        "successor_window_id": window,
        "budget_scope_window_ids": windows,
        "unknowns": prior["manifest"]["unknowns"],
        "inputs": [],
        "closed_executors_sha256": digest(witness),
        "predecessor_carry_sha256": prior["carry_sha256"],
        "budget_baseline_sha256": digest(
            {
                key: value
                for key, value in view["model_latest"].items()
                if store.one("SELECT window_id FROM model_admissions WHERE request_id=?", (key,))[
                    "window_id"
                ]
                in windows
            }
        ),
    }


def fold(store, row, record_sha, journal_id, authorities, latest, blocked, closures, carries):
    if set(row) != model_carry.CARRY_FIELDS or not hash_value(row["authorization_sha256"]):
        raise ValueError("model renewal record")
    value = row["manifest"]
    fields = {
        "authority",
        "core_stop_sha256",
        "budget_journal_id",
        "successor_window_id",
        "budget_scope_window_ids",
        "unknowns",
        "inputs",
        "closed_executors_sha256",
        "predecessor_carry_sha256",
        "budget_baseline_sha256",
    }
    prior = carries.get(value.get("predecessor_carry_sha256"))
    if (
        set(value) != fields
        or prior is None
        or digest(value) != row["carry_sha256"]
        or row["carry_sha256"] in carries
        or value["budget_journal_id"] != journal_id
        or value["inputs"] != []
        or blocked
        or not authorities
        or value["closed_executors_sha256"] != digest(row["closed_executors"])
        or value["unknowns"] != prior["manifest"]["unknowns"]
        or value["budget_scope_window_ids"]
        != sorted(
            set(prior["manifest"]["budget_scope_window_ids"])
            | {prior["manifest"]["successor_window_id"]}
        )
        or value["successor_window_id"] in value["budget_scope_window_ids"]
    ):
        raise ValueError("model renewal custody")
    authority = value["authority"]
    origin = prior["manifest"]["authority"]
    revisions = [r for r in authorities if r["revision"] >= origin["revision"] + 2]
    if (
        authority["grant_id"] != origin["grant_id"]
        or authority["restore_generation"] != origin["restore_generation"]
        or authority["policy_sha256"] != prior["successor_policy_sha256"]
        or len(revisions) != authority["revision"] - origin["revision"]
        or len(revisions) < 2
        or authority != owner_authority.reference(revisions[-2]).as_dict()
        or revisions[-1]["record_sha256"] != value["core_stop_sha256"]
        or revisions[-1]["revision"] != authority["revision"] + 1
        or any(
            r["state"] != ("ACTIVE" if index % 2 == 0 else "STOPPED")
            or r["policy_sha256"] != prior["successor_policy_sha256"]
            or r["restore_generation"] != origin["restore_generation"]
            for index, r in enumerate(revisions)
        )
    ):
        raise ValueError("model renewal authority chain")
    if sorted(k for k, v in latest.items() if v["state"] == "UNKNOWN") != [
        item["request_id"] for item in value["unknowns"]
    ] or any(v["state"] == "RESERVED" for v in latest.values()):
        raise ValueError("model renewal liability inventory")
    for item in value["unknowns"]:
        financial = latest[item["request_id"]]
        if (
            item["request_id"] not in closures
            or financial["kind"] != "settle"
            or paid_budget.sha(financial) != item["settlement_sha256"]
            or financial["minimum_micro_usd"] != item["minimum_micro_usd"]
        ):
            raise ValueError("model renewal original liability")
    baseline = {
        key: data
        for key, data in latest.items()
        if store.one("SELECT window_id FROM model_admissions WHERE request_id=?", (key,))[
            "window_id"
        ]
        in value["budget_scope_window_ids"]
    }
    if (
        digest(baseline) != value["budget_baseline_sha256"]
        or row["concurrency_basis"] != model_carry.wire.MODE
        or type(row["concurrency"]) is not int
        or row["concurrency"] != 1
        or any(
            not hash_value(row[key])
            for key in (
                "successor_policy_sha256",
                "successor_paid_contract_sha256",
                "successor_approval_sha256",
            )
        )
    ):
        raise ValueError("model renewal approval")
    carries[row["carry_sha256"]] = {**row, "record_sha256": record_sha}


def check_policy(store, settings, old, new, now):
    """Only expiry and explicitly bounded private Life consent may change."""
    from companion import owner_input_state
    from companion.owner_authority_protocol import OwnerCapture

    expiry = settings.paid_model.expires_at
    if new.expires_at != expiry or old.expires_at is not None and expiry <= old.expires_at:
        raise CoreError("OWNER_MODEL_RENEWAL_EXPIRY_REQUIRED", 409)
    old_purposes, new_purposes = set(old.purposes), set(new.purposes)
    if not old_purposes <= new_purposes or not new_purposes - old_purposes <= {"life"}:
        raise CoreError("OWNER_MODEL_CARRY_APPROVAL_CHANGED", 409)
    scope = new.life_shadow
    if scope is None:
        if old.life_shadow is not None or new_purposes != old_purposes:
            raise CoreError("OWNER_MODEL_CARRY_APPROVAL_CHANGED", 409)
        return
    if (
        "proactive" not in old_purposes
        or scope.authorization_sha256 != new.authorization_sha256
        or scope.expires_at > expiry
        or scope.expires_at <= datetime.fromisoformat(now)
        or scope.purposes != ["PRIVATE_MESSAGE_DRAFT"]
        or scope.automatic_actions != ["qq.private.proactive"]
        or scope.qzone_draft_sources
    ):
        raise CoreError("OWNER_MODEL_RENEWAL_LIFE_SCOPE_REQUIRED", 409)
    if old.life_shadow is not None:
        before, after = (s.model_dump(mode="json") for s in (old.life_shadow, scope))
        allowed = {"authorization_sha256", "expires_at"}
        if {k: v for k, v in before.items() if k not in allowed} != {
            k: v for k, v in after.items() if k not in allowed
        }:
            raise CoreError("OWNER_MODEL_RENEWAL_LIFE_SCOPE_REQUIRED", 409)
    for event_id in scope.source_event_ids:
        current = owner_authority._latest(store)
        row = store.one(
            "SELECT e.*,c.owner_actor_id,c.scope FROM events e "
            "JOIN conversations c USING(conversation_id) WHERE event_id=?",
            (event_id,),
        )
        capture = store.one(
            "SELECT value FROM runtime_meta WHERE key=?", ("owner_input:" + event_id,)
        )
        captured = json.loads(capture["value"])["capture"] if capture else None
        history = old.history
        if (
            not row
            or row["redacted"]
            or row["privacy_scope"] != "private"
            or row["scope"] != "private"
            or row["owner_actor_id"] != "owner"
            or row["actor_id"] != "owner"
            or row["source"] != "qq.onebot"
            or row["conversation_id"] != scope.conversation_id
            or captured is None
            or any(
                captured["authority"][key] != current[key]
                for key in ("grant_id", "restore_generation")
            )
            or row["occurred_at"] > now
            or row["ingested_at"] > now
            or history.scope != "owner_private"
            or history.not_before is None
            or datetime.fromisoformat(row["occurred_at"]) < history.not_before
            or (
                datetime.fromisoformat(now) - datetime.fromisoformat(row["ingested_at"])
            ).total_seconds()
            >= min(old.source_retention_days, settings.raw_event_retention_days) * 86400
        ):
            raise CoreError("OWNER_MODEL_RENEWAL_LIFE_SOURCE_REQUIRED", 409)
        state = owner_input_state.read(store, settings, OwnerCapture.model_validate(captured))
        if any(state[key] for key in ("unsettled_model", "unsettled_work", "unsettled_delivery")):
            raise CoreError("OWNER_MODEL_RENEWAL_LIFE_SOURCE_REQUIRED", 409)
