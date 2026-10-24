"""Earlier native ACK records retain a bounded, restart-safe pre-lease wait."""

from companion.config import SegmentedReplyConfig
from tests.conftest import run
from tests.native_continuous_support import answer, frame, host


def test_ack_without_manifest_uses_existing_maximum_interval_after_restart(core, tmp_path):
    async def source(ws):
        await ws.send(frame())
        await ws.wait_closed()

    async def scenario():
        policy = SegmentedReplyConfig(max_segments="unlimited", mode="semantic", interval_ms=600)
        async with host(core, tmp_path, source, policy=policy, ticking=False,
            model_reply=lambda _: answer("好呀\n接着说")) as (active, _, sends, _):
            await active.capture()
            await active.flush()
            assert await core.run_once()
            append = active.runtime.append

            def old_writer(kind, data):
                return append(kind, {key: value for key, value in data.items()
                                     if key != "reply_segment"})

            active.runtime.append = old_writer
            delayed = False

            async def delay(response):
                nonlocal delayed
                if response.request.url.path.endswith("/result") and not delayed:
                    assert response.status_code == 200
                    core.clock.advance(0.1)
                    delayed = True

            active.client.event_hooks["response"].append(delay)
            assert await active.pump() and len(sends) == 1
            active.restart()
            core.clock.advance(0.5)
            assert not await active.pump()
            assert len(sends) == 1 and active.attempts() == {"ACK": 1}
            assert core.store.one("SELECT state FROM outbox WHERE segment_index=1") == {
                "state": "PENDING"}
            core.clock.advance(2.5)
            assert await active.pump() and len(sends) == 2
            assert active.attempts() == {"ACK": 2}
    run(scenario())
