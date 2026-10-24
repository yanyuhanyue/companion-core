"""Original Core-leased delivery and NOT_SENT receipts for Meme pin tests."""

from companion_onebot_bridge import journal
from companion_onebot_bridge.owner_send import OwnerSendGuard
from tests.conftest import run
from tests.sdk_reader_fixture import Provider, answer, worker_for
from tests.support.native_meme import capture, case


def delivery(runtime, event, candidate):
    async def scenario():
        fixture = case(runtime)
        async with fixture.client(runtime) as client:
            assert await runtime.window.flush_one(client)
            provider = Provider(answer("太棒了。⬡" + candidate + "⬡"))
            async with worker_for(fixture.core, provider) as worker:
                assert await worker.run_once("native-meme-pin-fixture")
            assert len(provider.requests) == 1
            response = await client.post("/internal/v1/deliveries/lease", json={
                "worker_id": "meme-fixture", "authority": runtime.authority, "limit": 1})
            assert response.status_code == 200, response.text
            item = response.json()["items"][0]
            assert item["content"]["image"]["candidate_id"] == candidate
            return item

    return run(scenario())


def cancellation_proof(runtime, item):
    async def scenario():
        async with case(runtime).client(runtime) as client:
            guard = OwnerSendGuard(runtime.window, client)
            guard.reserve(item)
            response = await client.post(
                "/internal/v1/deliveries/" + item["message_id"] + "/begin-send",
                json={"lease_token": item["lease_token"]})
            assert response.status_code == 200, response.text
            runtime.spool.start_delivery(item)
            # An authenticated later frame is the real pre-dispatch cancellation cause.
            capture(runtime, 43, text="等一下，先别发送刚才的回复")
            assert await guard.cancel_before_send(item)
            records, _ = journal.read(runtime.window.path)
            receipt = next(record["data"]["core_receipt"] for record in reversed(records)
                           if record["kind"] == "NOT_SENT")
            assert runtime.db.execute("SELECT state FROM window_attempts").fetchone()[0] == (
                "NOT_SENT")
            return guard, receipt

    return run(scenario())
