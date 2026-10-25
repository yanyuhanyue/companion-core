"""Original caption takeover maintains every staged source tuple atomically."""

import base64
from contextlib import asynccontextmanager

from companion import budget, meme_asset_migration, meme_assets
from companion.contracts import CoreError
from companion.store import Store, encode
from tests.conftest import run
from tests.test_meme_core import body, caption_environment, worker


@asynccontextmanager
async def lineage_case(core, tmp_path, monkeypatch):
    async with caption_environment(core, tmp_path, monkeypatch) as values:
        event, _, client, _, _, now, attempt = values
        ids, payloads = [], []
        for name in ("A", "B", "C", "D"):
            payload = body(core, event, attempt, now)
            payload["source_evidence"]["usage_basis"] += "; original source " + name
            from companion_meme_protocol import digest

            payload["asset"]["source_evidence_sha256"] = digest(payload["source_evidence"])
            response = await client.post("/internal/v1/memes/captions", json=payload)
            assert response.status_code == 200, response.text
            ids.append(response.json()["job_id"])
            payloads.append(payload)
        custody = meme_assets.original_custody(core.store, core.settings, event["event_id"])
        assets = []
        with core.store.transaction():
            core.store.execute("UPDATE meme_captions SET reuse_job_id=? WHERE job_id=?",
                               (ids[2], ids[3]))
            for payload in payloads[1:]:
                proof = payload["source_evidence"]
                meta = {key: proof[key] for key in (
                    "source_page", "asset_url", "license", "usage_basis", "fetched_at", "revision"
                )}
                meta.update(category="like", description="Original untrusted hint", tags=[],
                            suitable=[], avoid=[], acquisition_json=encode(proof["acquisition"]))
                item = {"asset_sha256": payload["asset"]["asset_sha256"],
                        "source_sha256": payload["asset"]["source_evidence_sha256"],
                        "active": True, "metadata": meta, "mime": "image/png"}
                data = base64.b64decode(payload["asset"]["data_base64"])
                staged = meme_asset_migration._apply(
                    core.store, core.settings, {"custody": custody}, [(item, data)]
                )[0]
                assert staged["state"] == "STAGED"
                assets.append((staged["asset_id"], item, data))
        yield event, ids, assets, custody


async def reject_original(core, monkeypatch):
    def reject(*args, **kwargs):
        raise CoreError("MODEL_BUDGET_EXCEEDED", 429)

    with monkeypatch.context() as patch:
        patch.setattr(budget, "reserve", reject)
        assert await worker(core, [])
    assert not core.store.rows("SELECT * FROM model_usage")


def test_takeover_extends_same_event_and_transitive_sources_without_rewriting_originals(
    core, tmp_path, monkeypatch
):
    async def scenario():
        async with lineage_case(core, tmp_path, monkeypatch) as (event, ids, assets, custody):
            original_assets = core.store.rows("SELECT * FROM meme_assets ORDER BY asset_id")
            original_capture = core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                              ("owner_input:" + event["event_id"],))
            await reject_original(core, monkeypatch)
            calls = []
            assert await worker(core, calls)
            assert core.store.one("SELECT reuse_job_id FROM meme_captions WHERE job_id=?",
                                  (ids[1],))["reuse_job_id"] is None
            assert core.store.one("SELECT reuse_job_id FROM meme_captions WHERE job_id=?",
                                  (ids[2],))["reuse_job_id"] == ids[1]
            for asset_id, item, data in assets:
                sources, withdrawn = meme_assets.provenance(core.store, core.settings, custody,
                    item, data, caption_job_id=ids[assets.index((asset_id, item, data)) + 1])
                recorded = {(r["event_id"], r["caption_job_id"]) for r in core.store.rows(
                    "SELECT * FROM meme_asset_sources WHERE asset_id=?", (asset_id,))}
                assert sources and not withdrawn and set(sources) <= recorded
                assert (event["event_id"], ids[0]) in recorded  # Old root remains attributable.
                assert (event["event_id"], ids[1]) in recorded  # Same event, new caption tuple.
            assert await worker(core, calls) and await worker(core, calls)
            assert len(calls) == len(core.store.rows("SELECT * FROM model_usage")) == 1
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            assert core.store.rows("SELECT * FROM meme_assets ORDER BY asset_id") == original_assets
            assert core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                 ("owner_input:" + event["event_id"],)) == original_capture

    run(scenario())


def test_source_link_failure_rolls_back_graph_and_links_before_any_paid_work(
    core, tmp_path, monkeypatch
):
    async def scenario():
        async with lineage_case(core, tmp_path, monkeypatch) as (_, ids, assets, _):
            await reject_original(core, monkeypatch)
            before = core.store.rows("SELECT * FROM meme_asset_sources ORDER BY asset_id,"
                                     "event_id,caption_job_id")
            execute = Store.execute

            def fail_new_tuple(store, sql, params=()):
                if (sql.startswith("INSERT OR IGNORE INTO meme_asset_sources")
                    and params[0] == assets[1][0] and params[2] == ids[1]):
                    raise ValueError("Synthetic attribution write failure")
                return execute(store, sql, params)

            calls = []
            with monkeypatch.context() as patch:
                patch.setattr(Store, "execute", fail_new_tuple)
                assert await worker(core, calls)
            assert not calls and not core.store.rows("SELECT * FROM model_usage")
            assert core.store.one("SELECT reuse_job_id FROM meme_captions WHERE job_id=?",
                                  (ids[1],))["reuse_job_id"] == ids[0]
            assert core.store.one("SELECT reuse_job_id FROM meme_captions WHERE job_id=?",
                                  (ids[2],))["reuse_job_id"] == ids[0]
            assert core.store.rows("SELECT * FROM meme_asset_sources ORDER BY asset_id,"
                                   "event_id,caption_job_id") == before
            assert core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                                  (ids[1],)) == {"state": "CANCELLED",
                                                "last_error_code": "MEME_ASSET_SOURCE_CHANGED"}

    run(scenario())


def test_actual_paid_job_prevents_takeover_when_a_cached_usage_pointer_is_missing(
    core, tmp_path, monkeypatch
):
    async def scenario():
        async with lineage_case(core, tmp_path, monkeypatch) as (_, ids, _, _):
            async def fail_response():
                raise ValueError("Synthetic response lost")

            calls = []
            assert await worker(core, calls, hook=fail_response)
            before = core.store.rows("SELECT * FROM model_usage")
            assert len(calls) == len(before) == 1 and before[0]["state"] == "UNKNOWN"
            core.store.execute("UPDATE meme_captions SET usage_request_id=NULL WHERE job_id=?",
                               (ids[0],))
            assert await worker(core, calls)
            assert len(calls) == 1 and core.store.rows("SELECT * FROM model_usage") == before
            assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                  (ids[1],))["state"] == "CANCELLED"

    run(scenario())
