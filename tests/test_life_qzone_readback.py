"""Readback integrity of a real synthetic native-worker QZone review artifact."""

import copy
import json

from fastapi.testclient import TestClient

from companion import paid_budget
from companion.api import create_app
from companion.life_protocol import LifeQZoneDraft
from companion.store import encode
from tests.test_life_worker_integration import (
    test_real_http_source_to_independent_worker_shadow as produce,
)


def test_tampered_or_revoked_artifacts_are_hidden_individually_without_readback_writes(
    core,
    tmp_path,
    monkeypatch,
):
    produce(core, tmp_path, monkeypatch, "qzone")
    original = core.store.one("SELECT * FROM life_activity_results")
    proposal = json.loads(original["proposal_json"])
    draft = LifeQZoneDraft.model_validate(proposal["qzone_draft"])
    assert draft.content_sha256 == paid_budget.sha({"text": draft.text})
    assert draft.source_snapshot_sha256 == paid_budget.sha(
        {g.event_id: g.source_sha256 for g in draft.sources}
    )
    original_approval = core.settings.live_text_window
    source = core.store.one("SELECT * FROM events WHERE event_id='evt_test_1'")
    usage = core.store.rows("SELECT * FROM model_usage")
    jobs = core.store.rows("SELECT * FROM jobs")
    app = create_app(core.settings)
    app.state.core = core
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer " + core.settings.control_token.get_secret_value()}
    assert client.get("/internal/v2/life/topics").status_code == 401
    observations = []
    for case in (
        "valid",
        "text_tampered",
        "content_hash_tampered",
        "source_hash_tampered",
        "duplicate_sources",
        "audience_tampered",
        "owner_tampered",
        "purpose_tampered",
        "grant_revision_tampered",
        "expired_draft",
        "extended_expiry",
        "missing_draft",
        "wrong_draft_id",
        "boolean_version",
        "malformed_json",
        "changed_retained_text",
        "grant_revoked",
        "grant_revised",
        "source_changed",
        "source_withdrawn",
        "natural_expiry",
    ):
        value = copy.deepcopy(proposal)
        nested = value["qzone_draft"]
        core.settings.live_text_window = original_approval
        core.store.execute(
            "UPDATE events SET body_json=?,redacted=0 WHERE event_id=?",
            (source["body_json"], source["event_id"]),
        )
        core.store.execute("UPDATE life_activity_results SET text=?", (original["text"],))
        if case == "text_tampered":
            nested["text"] = "篡改后的合成正文"
        elif case == "content_hash_tampered":
            nested["content_sha256"] = "0" * 64
        elif case == "source_hash_tampered":
            nested["source_snapshot_sha256"] = "0" * 64
        elif case == "duplicate_sources":
            nested["sources"].append(copy.deepcopy(nested["sources"][0]))
        elif case == "audience_tampered":
            nested["audience"] = "public"
        elif case == "owner_tampered":
            nested["owner_qq_id"] = "99999"
        elif case == "purpose_tampered":
            nested["purpose"] = "qzone_publish"
        elif case == "grant_revision_tampered":
            nested["sources"][0]["revision"] = 99
        elif case in {"expired_draft", "extended_expiry"}:
            nested["expires_at"] = (
                "2020-01-01T00:00:00Z" if case == "expired_draft" else "2030-01-01T00:00:00Z"
            )
        elif case == "missing_draft":
            del value["qzone_draft"]
        elif case == "wrong_draft_id":
            nested["draft_id"] = "job_unrelated"
        elif case == "boolean_version":
            nested["schema_version"] = True
        elif case == "changed_retained_text":
            core.store.execute("UPDATE life_activity_results SET text='改写存储正文'")
        elif case in {"grant_revoked", "grant_revised"}:
            scope = original_approval.life_shadow
            grants = (
                []
                if case == "grant_revoked"
                else [scope.qzone_draft_sources[0].model_copy(update={"revision": 2})]
            )
            core.settings.live_text_window = original_approval.model_copy(
                update={"life_shadow": scope.model_copy(update={"qzone_draft_sources": grants})}
            )
        elif case == "source_changed":
            core.store.execute(
                "UPDATE events SET body_json=? WHERE event_id=?",
                (encode({"text": "变更后的合成来源"}), source["event_id"]),
            )
        elif case == "source_withdrawn":
            core.store.execute(
                "UPDATE events SET redacted=1 WHERE event_id=?", (source["event_id"],)
            )
        elif case == "natural_expiry":
            core.clock.advance(86401)
        core.store.execute(
            "UPDATE life_activity_results SET proposal_json=?",
            ("{" if case == "malformed_json" else encode(value),),
        )
        before = core.store.db.total_changes
        response = client.get("/internal/v2/life/topics", headers=headers)
        visible = (
            []
            if response.status_code != 200
            else [result for topic in response.json()["items"] for result in topic["results"]]
        )
        observations.append(
            {
                "case": case,
                "status": response.status_code,
                "visible": bool(visible),
                "read_only": before == core.store.db.total_changes,
            }
        )
        assert core.store.rows("SELECT * FROM outbox") == []
        assert core.store.rows("SELECT * FROM social_autonomy_plans") == []
        assert core.store.rows("SELECT * FROM model_usage") == usage
        assert core.store.rows("SELECT * FROM jobs") == jobs
    assert all(
        row["status"] == 200 and row["read_only"] and row["visible"] == (row["case"] == "valid")
        for row in observations
    ), observations
