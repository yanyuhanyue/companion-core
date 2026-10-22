"""Lease/result client. No storage, owner identity, delivery or model authority."""

import asyncio

import httpcore
import httpx

from source_reader.network import ReadBudget, ReadFailure


async def read_result(reader, item, validate_account_read=None):
    budget = ReadBudget(
        max_bytes=min(1048576, int(item["max_bytes"])),
        max_requests=min(8, int(item.get("max_requests", 1))),
        validate_account_read=validate_account_read,
    )
    result = {"lease_token": item["lease_token"], "status": "SUCCESS", "items": []}
    try:
        snapshot = getattr(reader, "grant_snapshot", lambda **kwargs: None)
        current_grant = snapshot(required=bool(item.get("source_grant")))
        if current_grant is not None:
            result["source_grant"] = current_grant
        if item.get("source_grant") is not None and current_grant != item["source_grant"]:
            raise ReadFailure("SOURCE_GRANT_REVISION_CHANGED")
        async with asyncio.timeout(20):
            result["items"] = await reader.read(item, budget)
        if snapshot(required=bool(current_grant)) != current_grant:
            raise ReadFailure("SOURCE_CREDENTIAL_REVOKED")
        if budget.used_bytes > budget.max_bytes:
            raise ReadFailure("READER_BYTE_LIMIT")
    except ReadFailure as error:
        result.update(status="FAILED", items=[], error_code=str(error))
    except (
        TimeoutError,
        OSError,
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
        httpx.HTTPError,
    ):
        result.update(status="UNKNOWN", items=[], error_code="READER_NETWORK_UNKNOWN")
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        result.update(status="FAILED", items=[], error_code="UPSTREAM_RESPONSE_INVALID")
    unknown = result["status"] == "UNKNOWN"
    result["used_requests"] = None if unknown else budget.used_requests
    result["used_bytes"] = (
        budget.used_bytes if not unknown and budget.used_bytes <= budget.max_bytes else None
    )
    return result


async def tick(core_client, reader, adapter_id):
    prefix = "/internal/v2/readers/" + adapter_id
    grant_snapshot = getattr(reader, "grant_snapshot", lambda **kwargs: None)
    snapshot = grant_snapshot(required=True)
    response = await core_client.post(prefix + "/lease", **(
        {"json": {"source_grant": snapshot}} if snapshot is not None else {}))
    response.raise_for_status()
    item = response.json()["item"]
    if item is None:
        return False
    if item.get("source_grant") != snapshot:
        raise ReadFailure("SOURCE_GRANT_LEASE_MISMATCH")
    async def validate_account_read():
        if grant_snapshot(required=True) != snapshot:
            raise ReadFailure("SOURCE_GRANT_REVISION_CHANGED")
        response = await core_client.post(prefix + "/" + item["request_id"] + "/validate", json={
            "lease_token": item["lease_token"], "source_grant": snapshot,
        })
        if response.status_code != 200:
            raise ReadFailure("SOURCE_GRANT_REVALIDATION_FAILED")
        decision = response.json()
        if (
            not isinstance(decision, dict) or set(decision) != {"valid"}
            or decision["valid"] is not True
        ):
            raise ReadFailure("SOURCE_GRANT_REVALIDATION_FAILED")
        if grant_snapshot(required=True) != snapshot:
            raise ReadFailure("SOURCE_GRANT_REVISION_CHANGED")

    result = await read_result(reader, item, validate_account_read)
    # An uncertain receipt is never followed by replaying public I/O locally.
    response = await core_client.post(prefix + "/" + item["request_id"] + "/result", json=result)
    response.raise_for_status()
    return True
