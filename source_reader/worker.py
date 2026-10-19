"""Lease/result client. No storage, owner identity, delivery or model authority."""

import asyncio

import httpcore
import httpx

from source_reader.network import ReadBudget, ReadFailure


async def read_result(reader, item):
    budget = ReadBudget(
        max_bytes=min(1048576, int(item["max_bytes"])),
        max_requests=min(8, int(item.get("max_requests", 1))),
    )
    result = {"lease_token": item["lease_token"], "status": "SUCCESS", "items": []}
    try:
        async with asyncio.timeout(20):
            result["items"] = await reader.read(item, budget)
        if budget.used_bytes > budget.max_bytes:
            raise ReadFailure("READER_BYTE_LIMIT")
    except ReadFailure as error:
        result.update(status="FAILED", items=[], error_code=str(error))
    except (
        TimeoutError,
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
        httpx.HTTPError,
    ):
        result.update(status="UNKNOWN", items=[], error_code="READER_NETWORK_UNKNOWN")
    except (ValueError, KeyError, TypeError, AttributeError):
        result.update(status="FAILED", items=[], error_code="UPSTREAM_RESPONSE_INVALID")
    unknown = result["status"] == "UNKNOWN"
    result["used_requests"] = None if unknown else budget.used_requests
    result["used_bytes"] = (
        budget.used_bytes if not unknown and budget.used_bytes <= budget.max_bytes else None
    )
    return result


async def tick(core_client, reader, adapter_id):
    prefix = "/internal/v2/readers/" + adapter_id
    response = await core_client.post(prefix + "/lease")
    response.raise_for_status()
    item = response.json()["item"]
    if item is None:
        return False
    result = await read_result(reader, item)
    # An uncertain receipt is never followed by replaying public I/O locally.
    response = await core_client.post(prefix + "/" + item["request_id"] + "/result", json=result)
    response.raise_for_status()
    return True
