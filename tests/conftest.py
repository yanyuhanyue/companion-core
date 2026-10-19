import asyncio
from datetime import datetime, timedelta

import pytest

from companion.config import Settings
from companion.contracts import InboundEvent
from companion.core import Core
from companion.model import ModelResponse
from companion.store import migrate


class Clock:
    def __init__(self):
        self.now = datetime.fromisoformat("2026-10-19T04:00:00+00:00")

    def __call__(self):
        return self.now.isoformat(timespec="microseconds")

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeModel:
    def __init__(self):
        self.calls = []
        self.error = None
        self.callback = None
        self.extraction = '{"memories":[]}'

    async def complete(self, messages, scenario):
        self.calls.append((messages, scenario))
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        text = (
            self.extraction if scenario == "memory_extract" else "回复：" + messages[-1]["content"]
        )
        return ModelResponse(text, 100, 20, 40)


def run(awaitable):
    return asyncio.run(awaitable)


def event(core, number=1, text="今天想聊聊茶", origin=None, at=None):
    cfg = core.settings
    origin = origin or f"qq:FriendMessage:{cfg.owner_qq_id}"
    return InboundEvent.model_validate(
        {
            "schema_version": 1,
            "event_id": f"evt_test_{number}",
            "source": "qq.onebot",
            "source_event_id": f"{cfg.bot_qq_id}:{origin}:{number}",
            "event_type": "message.received",
            "occurred_at": at or core.clock(),
            "conversation": {"platform_session_key": origin, "scope": "private"},
            "actor": {"platform_user_id": cfg.owner_qq_id, "role": "owner"},
            "payload": {"text": text, "media": []},
            "trace_id": f"trace_test_{number}",
        }
    )


def ingest(core, number=1, text="今天想聊聊茶", **kwargs):
    return core.ingest(event(core, number, text, **kwargs), f"key-{number}")


@pytest.fixture
def core(tmp_path):
    cfg = Settings(
        owner_qq_id="10001",
        bot_qq_id="10002",
        bridge_token="test-token-" * 4,
        database=str(tmp_path / "core.db"),
        erasure_ledger=str(tmp_path / "privacy.jsonl"),
        input_rate=300000,
        cached_input_rate=6000,
        output_rate=1200000,
        proactive_enabled=False,
    )
    migrate(cfg.database)
    instance = Core(cfg, FakeModel(), Clock())
    yield instance
    instance.store.close()
