"""Read back owner messages sent while the event socket was closed.

SnowLuma drops events while no WebSocket client is connected, so a Bridge restart loses
whatever the owner sent in those seconds. After each connect the Bridge reads the owner
conversation's latest messages through the same authenticated OneBot HTTP endpoint and
captures only owner messages that are newer than the last capture and still inside the
original 30 s freshness bound, as `onebot_history_backfill`. Everything else (Core checks,
reference reads, signatures, journal and inbox) is the live capture path unchanged.
"""

from datetime import UTC, datetime

import httpx

from . import journal
from .capture import MAX_AGE_SECONDS, prepare_owner_input
from .event_normalizer import decode, identifier, message_identity
from .metrics import emit
from .network import checked_http_client, private_request_matches, seal_protocol_content
from .onebot_client import OneBotClient
from .spool import encode

HISTORY_COUNT = 20
PEER = ("onebot-http-history", 0)
FIELDS = ("time", "message_type", "sub_type", "message_id", "user_id", "message",
          "raw_message", "font", "sender")


def _latest_capture(runtime):
    latest = None
    for (value,) in runtime.db.execute(
        "SELECT json_extract(capture_json,'$.occurred_at') FROM window_inputs WHERE plan_hash=? "
        "AND json_extract(capture_json,'$.kind') IN "
        "('authenticated_onebot_ws','onebot_history_backfill')",
        (journal.digest(runtime.authority),),
    ):
        occurred = datetime.fromisoformat(value)
        latest = occurred if latest is None or occurred > latest else latest
    return latest


async def _history(runtime, core, protocol):
    config = runtime.config
    expected = {"user_id": int(config.owner_qq_id), "count": HISTORY_COUNT}

    async def boundary(request):
        def validate():
            if (request.method != "POST" or request.url.path != "/get_friend_msg_history"
                or not private_request_matches(request, config.onebot_http_url, request.url.path)
                or request.headers.get("Authorization") != "Bearer " + config.onebot_token
                or decode(request.content, 256) != expected):
                raise ValueError("OWNER_PROTOCOL_REQUEST_CHANGED")

        validate()
        await runtime.check(core)
        validate()
        seal_protocol_content(request, request.content)

    client = checked_http_client(protocol, boundary)
    data = await OneBotClient(config, client).friend_history(HISTORY_COUNT)
    messages = data.get("messages")
    return messages if isinstance(messages, list) else []


def _frame(config, message):
    if not isinstance(message, dict) or not all(key in message for key in FIELDS[:6]):
        return None
    sender = message.get("sender")
    if (message["message_type"] != "private" or message["sub_type"] != "friend"
        or identifier(message["user_id"]) != config.owner_qq_id
        or not isinstance(sender, dict)
        or identifier(sender.get("user_id")) != config.owner_qq_id
        or type(message["time"]) is not int):
        return None
    value = {key: message[key] for key in FIELDS if key in message}
    return encode({**value, "post_type": "message", "self_id": int(config.bot_qq_id)})


async def backfill(runtime, core, onebot, wake):
    """Capture owner messages missed while disconnected; returns how many were added."""
    records = runtime.verify()
    latest = _latest_capture(runtime)
    # Core accepts captures only after the current authority was activated, which is
    # before this Bridge bound it; anything earlier would be refused and stop uploads.
    bound = max(datetime.fromisoformat(record["at"]) for record in records
                if record["kind"] in {"OWNER_BOUND", "OWNER_REBOUND"})
    try:
        messages = await _history(runtime, core, onebot.client)
    except (ValueError, httpx.HTTPError):
        # An unavailable or unexpected history read must not stop live receiving.
        emit("owner_backfill", status="UNAVAILABLE")
        return 0
    added = 0
    for message in sorted((m for m in messages if isinstance(m, dict)
                           and type(m.get("time")) is int), key=lambda m: m["time"]):
        frame = _frame(runtime.config, message)
        if frame is None:
            continue
        occurred = datetime.fromtimestamp(message["time"], UTC)
        received_at = runtime.clock()
        if (occurred < bound or latest is not None and occurred < latest
                or (received_at - occurred).total_seconds() > MAX_AGE_SECONDS):
            continue
        _, event_id = message_identity(runtime.config, message["message_id"])
        if runtime.db.execute("SELECT 1 FROM window_inputs WHERE event_id=?",
                              (event_id,)).fetchone():
            continue
        prepared = await prepare_owner_input(runtime, core, onebot, frame, received_at, PEER)
        if prepared is None:
            continue
        if runtime.persist_capture(prepared["event"], frame, received_at=received_at, peer=PEER,
                                   media=prepared["media"], backfill=True):
            added += 1
            wake.set()
    emit("owner_backfill", status="COMPLETE", added=added)
    return added
