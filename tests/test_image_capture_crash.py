"""A real child exit after durable preparation cannot reacquire an image or a model call."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from companion.contracts import CoreError
from companion.store import Store
from tests.conftest import run
from tests.test_live_image_input import options, png
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment

ROOT = Path(__file__).resolve().parents[1]

CHILD = """
import asyncio,json,os,sys
from pathlib import Path
from companion import budget
from companion.config import Settings
from companion.content import ContentService
from companion.observation import ObservationService
from companion.store import Store
value=json.loads(Path(sys.argv[1]).read_text('utf8'))
cfg=Settings.model_validate(value['settings'])
store=Store(cfg.database)
clock=lambda:value['clock']
content=ContentService(store,cfg,ObservationService(store,cfg,clock),clock=clock)
budget.reserve=lambda *args,**kwargs:os._exit(17)
asyncio.run(content.analyze_image(value['event_id'],'image:0',
    Path(sys.argv[2]).read_bytes(),'image/png',value['lease'],value['proof']))
"""


def test_child_exit_before_reserve_keeps_one_shot_content_custody(core, tmp_path):
    raw = raw_event(message=[image_segment()])

    async def source(ws):
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    async def actual():
        async with source_window(
            core,
            tmp_path,
            source,
            live=True,
            approved=True,
            rich_input_scope="references_v1",
            scope_options=options(),
        ) as (
            window,
            client,
            protocol,
            _,
            sends,
        ):
            core.content.options = core.settings.content
            await window.capture_once(client, protocol)

            async def crash_upload(request):
                if not request.url.path.endswith("/image"):
                    return
                settings = core.settings.model_dump(mode="python")
                value = {
                    "settings": settings,
                    "clock": core.clock(),
                    "event_id": request.url.path.split("/")[-2],
                    "lease": request.headers["X-Content-Lease"],
                    "proof": json.loads(request.headers["X-Content-Proof"]),
                }

                def serialize(item):
                    return (
                        item.get_secret_value() if isinstance(item, SecretStr) else item.isoformat()
                    )

                fixture = tmp_path / "child-input.json"
                fixture.write_text(json.dumps(value, default=serialize), encoding="utf8")
                data = tmp_path / "child-image.png"
                data.write_bytes(request.content)
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-c", CHILD, str(fixture), str(data)],
                    cwd=ROOT,
                    env=os.environ.copy(),
                    capture_output=True,
                    timeout=15,
                )
                assert result.returncode == 17, result.stderr.decode(errors="replace")
                raise ValueError("SYNTHETIC_CHILD_EXIT")

            client.event_hooks["request"].append(crash_upload)

            async def resolve(host, port):
                return ["1.1.1.1"]

            with pytest.raises(ValueError, match="SYNTHETIC_CHILD_EXIT"):
                await window.approve_captured(
                    client,
                    capture_approval(window),
                    image_resolver=resolve,
                    image_transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200, content=png(), headers={"Content-Type": "image/png"}
                        )
                    ),
                )
            with closing(Store(core.settings.database)) as reopened:
                assert reopened.one("SELECT state FROM content_items")["state"] == "RUNNING"
                assert not reopened.one("SELECT 1 FROM model_usage")
                assert not reopened.one("SELECT 1 FROM model_admissions")
            core.clock.now = datetime.fromisoformat(core.clock()) + timedelta(seconds=40)
            await core.content.run_once()
            assert core.store.one("SELECT state FROM content_items")["state"] == "UNKNOWN"
            with pytest.raises(CoreError):
                core.content.claim_upload(value_event_id(core), "image:0")
            assert sends == [] and not core.store.one("SELECT 1 FROM outbox")

    run(actual())


def value_event_id(core):
    return core.store.one("SELECT event_id FROM events WHERE source='qq.onebot'")["event_id"]
