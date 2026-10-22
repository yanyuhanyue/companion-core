"""Measure unchanged native FTS5+BGE on public synthetic facts and held-out queries.

Gold labels are read only after retrieval. No model API, QQ, real data, cache
download or algorithm tuning occurs. A local cache miss fails closed.
"""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

from companion import paid_budget
from companion.backup import backup, restore
from companion.config import Settings
from companion.contracts import Candidate
from companion.memory import Memory
from companion.privacy import ErasureLedger
from companion.retrieval import INDEX_VERSION, REPOSITORY, REVISION
from companion.store import Store, encode, migrate

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "docs/experiments/p12-memory/cases.json"
LEGACY = ROOT / "docs/experiments/p8b1-context-v2/legacy-cases.json"
LEGACY_SHA256 = "12a73ddac13a1188113c2c8a4840a6a19959a75425961e6d7a3eefd62f374d4c"


def evaluate(directory, embedding_cache):
    from huggingface_hub import snapshot_download

    cached = snapshot_download(REPOSITORY, revision=REVISION, cache_dir=str(embedding_cache),
                               allow_patterns=["*.json", "*.txt", "model_optimized.onnx"],
                               local_files_only=True)
    if not (Path(cached) / "model_optimized.onnx").is_file():
        raise ValueError("PINNED_LOCAL_EMBEDDING_CACHE_REQUIRED")
    cases = json.loads(CASES.read_text("utf-8"))
    if paid_budget.sha(json.loads(LEGACY.read_text("utf-8"))) != LEGACY_SHA256:
        raise ValueError("FROZEN_LEGACY_FIXTURE_CHANGED")
    cfg = Settings(owner_qq_id="10001", bot_qq_id="10002", bridge_token="synthetic-token-" * 4,
        database=str(directory / "core.db"), erasure_ledger=str(directory / "erasures.jsonl"),
        memory_enabled=True, proactive_enabled=False, embedding_cache_dir=str(embedding_cache))
    migrate(cfg.database)
    store, ledger = Store(cfg.database), ErasureLedger(cfg.erasure_ledger)
    try:
        ledger.initialize(store)
        now = "2026-10-22T04:00:00+00:00"
        with store.transaction():
            store.insert("principals", actor_id="owner", kind="owner", created_at=now)
            store.insert("conversations", conversation_id="conv_synthetic", platform="qq.onebot",
                platform_session_key="synthetic:FriendMessage:10001", scope="private",
                owner_actor_id="owner", created_at=now)
        memory = Memory(store, cfg, ledger)
        identities = {}
        facts = cases["facts"] + [{"id": f"distractor_{i}",
            "content": f"第{i}号合成档案的归档编号是编号{i:03}。", "day": "2026-10-21"}
            for i in range(40)]
        with store.transaction():
            for fact in facts:
                event_id = "evt_synthetic_" + fact["id"]
                stamp = fact["day"] + "T04:00:00+00:00"
                store.insert("events", event_id=event_id, schema_version=1, source="qq.onebot",
                    source_event_id=event_id, conversation_id="conv_synthetic", actor_id="owner",
                    event_type="message.received", occurred_at=stamp, ingested_at=stamp,
                    privacy_scope="private", body_json=encode({"text": fact["content"]}),
                    trace_id=event_id, memory_processed=1)
                identities[fact["id"]] = memory.add(Candidate(subject="user", kind="fact",
                    fact_key=fact["id"], content=fact["content"], evidence_event_id=event_id,
                    evidence_quote=fact["content"]), stamp, confirmed=True)
        inverse = {value: key for key, value in identities.items()}
        rows = []
        for case in cases["queries"]:
            # Neither candidate IDs nor expected labels are supplied to native retrieval.
            found = memory.search(case["query"])
            actual = [inverse[row["memory_id"]] for row in found]
            required, allowed = set(case["required"]), set(case["allowed"])
            rows.append({"id": case["id"], "split": case["split"], "retrieved": actual,
                "required": sorted(required), "missing": sorted(required - set(actual)),
                "false_positive": sorted(set(actual) - allowed),
                "retrieved_utf8_bytes": sum(len(row["content"].encode()) for row in found)})
        original = identities["tea"]
        corrected = memory.decide(original, "correct", now,
                                   "主人现在最喜欢的饮料是热可可。")["memory_id"]
        correction_hits = memory.search("我最喜欢的饮料是什么？")
        correction = {"new_value_recalled": corrected in {r["memory_id"] for r in correction_hits},
                      "superseded_leaks": sum(r["memory_id"] == original for r in correction_hits)}
        archive, restored = directory / "before.enc", directory / "restored.db"
        key = Fernet.generate_key()
        backup(cfg.database, archive, key)
        with store.transaction():
            ledger.expire_events(store, ["evt_synthetic_cat"], now)
        memory.decide(corrected, "forget", now)
        restore(archive, restored, key, cfg.erasure_ledger)
        erased_ids = {original, corrected, identities["cat"]}
        erasure = []
        restored_store = Store(str(restored))
        try:
            for label, active in (("current", store), ("old_backup_latest_ledger", restored_store)):
                active.execute("DELETE FROM memory_vectors")
                active.execute("DELETE FROM memories_fts")
                active.execute("INSERT INTO memories_fts(memory_id,content) "
                               "SELECT memory_id,content "
                               "FROM memories WHERE status IN ('HYPOTHESIS','CONFIRMED')")
                rebuilt = Memory(active, cfg, ledger)
                hits = [row for query in ("绿茶", "热可可", "猫叫什么")
                        for row in rebuilt.search(query)]
                erasure.append({"state": label,
                    "erased_memory_hits": sum(row["memory_id"] in erased_ids for row in hits),
                    "erased_content_hits": sum(any(text in row["content"] for text in
                                                  ("绿茶", "热可可", "团子")) for row in hits),
                    "erased_vector_rows": active.one(
                        "SELECT COUNT(*) n FROM memory_vectors WHERE memory_id IN "
                        "(SELECT value FROM json_each(?))", (encode(sorted(erased_ids)),))["n"],
                    "unrelated_fact_retained": identities["cover"] in {
                        row["memory_id"] for row in rebuilt.search("独立项目的封面颜色")}})
        finally:
            restored_store.close()
        summaries = {}
        for split in ("legacy_query_probe", "holdout"):
            sample = [row for row in rows if row["split"] == split]
            required = sum(len(row["required"]) for row in sample)
            missing = sum(len(row["missing"]) for row in sample)
            false_positive = sum(len(row["false_positive"]) for row in sample)
            returned = sum(len(row["retrieved"]) for row in sample)
            summaries[split] = {"queries": len(sample), "required_memories": required,
                "necessary_memories_recalled": required - missing, "missing": missing,
                "retrieved_memories": returned, "false_positive_candidates": false_positive,
                "required_recall": (required - missing) / required if required else None,
                "candidate_precision": (returned - false_positive) / returned if returned else None}
        return {"evidence": "OFFLINE_SYNTHETIC_NATIVE_RETRIEVAL", "embedding": INDEX_VERSION,
            "algorithm_changed": False, "seed_facts": len(facts),
            "input_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
            "retrieval_source_sha256": hashlib.sha256(
                (ROOT / "companion/retrieval.py").read_bytes()).hexdigest(),
            "memory_source_sha256": hashlib.sha256(
                (ROOT / "companion/memory.py").read_bytes()).hexdigest(),
            "frozen_context_baseline": {"baseline": "7/8", "experiment": "7/8",
                "rerun": False, "fixture_sha256": LEGACY_SHA256,
                "comparability": "Historical context metric vs. new retrieval-candidate metric"},
            "splits": summaries, "cases": rows, "correction": correction,
            "source_erasure_and_index_rebuild": erasure,
            "real_model_usage": None, "real_token_cost": None, "real_phone_samples": None,
            "real_natural_days": None, "model_http_attempts": 0,
            "decision": "KEEP_NATIVE_RETRIEVAL_NO_GRAPH_ADMISSION"}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("WORKSPACE_OUTPUT_REQUIRED")
    temporary = ROOT / ".local/test-temp"
    temporary.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p12-retrieval-", dir=temporary) as directory:
        report = evaluate(Path(directory), args.embedding_cache.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(encode({"evidence": report["evidence"], "splits": report["splits"],
                  "source_erasure_and_index_rebuild": report["source_erasure_and_index_rebuild"]}))


if __name__ == "__main__":
    main()
