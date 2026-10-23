"""Independent review regressions for input lifecycle HTTP boundaries."""

from companion import live_text
from tests.conftest import run
from tests.test_input_protocol import admit, environment, notice


def test_unrelated_recall_preserves_exact_input_approval_replay(core, tmp_path, monkeypatch):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, now):
            _, first = await admit(client, config, plan, now)
            assert first.status_code == 202, first.text
            event, second = await admit(client, config, plan, now, 2)
            assert second.status_code == 202, second.text
            approved = live_text._meta(core.store, live_text.INPUT + event["event_id"])
            replay = {key: value for key, value in approved.items() if key != "privacy_epoch"}

            recalled = await client.post(
                "/internal/v1/live-text/inputs/recall", json=notice(config, plan, now),
            )
            assert recalled.status_code == 200, recalled.text
            assert recalled.json()["state"] == "RECALLED"
            assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                  (event["event_id"],))["redacted"] == 0

            repeated = await client.post("/internal/v1/live-text/inputs/approve", json=replay)
            assert repeated.status_code == 200, repeated.text
            assert repeated.json() == {"approved": True}
            assert live_text._meta(core.store, live_text.INPUT + event["event_id"]) == approved

    run(scenario())
