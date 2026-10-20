"""Local input proof and append-only durability extension for the existing usage ledger.

No downloads and no provider I/O. A pinned tokenizer is useful only with an approved
provider rendering contract; a local artifact alone does not prove remote framing.
"""

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path

from tokenizers import Tokenizer

from companion.contracts import CoreError
from companion.store import encode, uid


def sha(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def snapshot(settings):
    approval = settings.paid_model
    value = approval.model_dump(mode="json", exclude={"tokenizer_path", "journal_path"})
    # Private per-window credential binding, never the raw key or a public account identifier.
    value["credential_tag"] = hmac.new(
        settings.model_key.get_secret_value().encode(),
        encode([approval.window_id, approval.provider, approval.endpoint]).encode(),
        hashlib.sha256,
    ).hexdigest()
    return value


def contract(settings, now):
    approval = settings.paid_model
    if approval is None:
        raise CoreError("PAID_APPROVAL_REQUIRED", 503)
    if not settings.model_key.get_secret_value():
        raise CoreError("PAID_MODEL_KEY_REQUIRED", 503)
    current = datetime.fromisoformat(now)
    if current.tzinfo is None or not approval.starts_at <= current < approval.expires_at:
        raise CoreError("PAID_APPROVAL_EXPIRED", 503)
    if approval.rates_valid_until <= current:
        raise CoreError("PAID_RATES_EXPIRED", 503)
    if (
        settings.provider,
        settings.model_name,
        settings.model_url.rstrip("/"),
        settings.timezone,
    ) != (approval.provider, approval.model, approval.endpoint.rstrip("/"), approval.timezone):
        raise CoreError("PAID_PROVIDER_CONTRACT_MISMATCH", 503)
    if (settings.input_rate, settings.cached_input_rate, settings.output_rate) != (
        approval.input_rate,
        approval.cached_input_rate,
        approval.output_rate,
    ):
        raise CoreError("PAID_RATE_MISMATCH", 503)
    return approval


def input_bound(approval, messages, scenario):
    try:
        data = Path(approval.tokenizer_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != approval.tokenizer_sha256:
            raise ValueError("hash")
        tokenizer = Tokenizer.from_str(data.decode("utf-8"))
        # Truncation would disguise input overflow; padding is not provider input proof.
        if tokenizer.truncation is not None or tokenizer.padding is not None:
            raise ValueError("tokenizer truncation/padding")
        prompt = ""
        for message in messages:
            if (
                set(message) != {"role", "content"}
                or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise ValueError("unsupported message contract")
            prompt += "<|im_start|>" + message["role"] + "\n" + message["content"] + "<|im_end|>\n"
        if scenario in {"memory_review", "memory_extract", "diary", "reflection"}:
            prompt += approval.structured_suffix
        prompt += "<|im_start|>assistant\n"
        count = len(tokenizer.encode(prompt, add_special_tokens=True).ids)
        if not count:
            raise ValueError("empty tokenizer result")
        return count
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        raise CoreError("MODEL_INPUT_BOUND_UNPROVEN", 503) from error


def journal(store, approval, record=None, *, allow_settlement=False):
    """Caller holds BEGIN IMMEDIATE; fsync before DB commit makes failure conservative.

    This is not a second queue or message store: only reservation hashes and monetary
    lower bounds survive outside the DB. A mismatch blocks, never silently repairs.
    """
    path = Path(approval.journal_path)
    identity = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
    try:
        if not path.exists():
            if identity or store.one("SELECT 1 FROM model_admissions LIMIT 1"):
                raise ValueError("missing independent journal")
            path.parent.mkdir(parents=True, exist_ok=True)
            journal_id = uid("budget")
            header = {"kind": "header", "journal_id": journal_id, "previous": "0" * 64}
            header["sha256"] = sha(header)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                os.chmod(path, 0o600)
                stream.write(encode(header) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            store.insert("runtime_meta", key="model_budget_journal_id", value=journal_id)
            identity = {"value": journal_id}
        lines = path.read_text("utf-8").splitlines()
        previous, latest, blocked = "0" * 64, {}, False
        for index, line in enumerate(lines):
            row = json.loads(line)
            digest = row.pop("sha256")
            if row.get("previous") != previous or sha(row) != digest:
                raise ValueError("journal chain")
            previous = digest
            if index == 0:
                if (
                    row.get("kind") != "header"
                    or not identity
                    or row.get("journal_id") != identity["value"]
                ):
                    raise ValueError("journal identity")
            else:
                latest[row["request_id"]] = row
                blocked |= row["kind"] == "blocked"
        if not lines or (blocked and not allow_settlement):
            raise ValueError("journal reconciliation")
        for request_id, row in latest.items():
            if record and request_id == record["request_id"]:
                continue
            usage = store.one(
                "SELECT billed_micro_usd FROM model_usage WHERE request_id=?", (request_id,)
            )
            admission = store.one(
                "SELECT contract_sha256,io_started_at FROM model_admissions WHERE request_id=?",
                (request_id,),
            )
            if (
                not usage
                or not admission
                or usage["billed_micro_usd"] < row["minimum_micro_usd"]
                or admission["contract_sha256"] != row["contract_sha256"]
                or admission["io_started_at"] != row.get("io_started_at")
            ):
                raise ValueError("DB behind journal")
        # A DB from a different journal cannot accept an older valid prefix.
        ids = {r["request_id"] for r in store.rows("SELECT request_id FROM model_admissions")}
        if record:
            ids.discard(record["request_id"])
        if set(latest) - ({record["request_id"]} if record else set()) != ids:
            raise ValueError("journal behind DB")
        if record is not None:
            row = {**record, "previous": previous}
            row["sha256"] = sha(row)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encode(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503) from error
