"""Reproduce a frozen public-synthetic candidate experiment, never business M1.

The retrieval algorithm and corpus were committed before the new holdout was
authored. Gold labels are opened only after every method has returned candidates.
Only a pinned local embedding cache is allowed; this script creates its own DB.
"""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from time import perf_counter_ns

from cryptography.fernet import Fernet

from companion.backup import backup, restore
from companion.config import Settings
from companion.contracts import Candidate
from companion.memory import Memory
from companion.privacy import ErasureLedger
from companion.retrieval import INDEX_VERSION, REPOSITORY, REVISION
from companion.store import Store, encode, migrate
from scripts.p13_memory_research import active_annotations, evidence_pool, retrieve

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/experiments/p13-memory"
METHODS = ("fts", "fts_bge", "bounded_tags_onehop")
NOW = "2026-10-22T04:00:00+00:00"
FREEZE_COMMIT = "df617a5b4dede29fcf1884717f5cc2a997a0960d"
FROZEN_FILES = {
    "scripts/p13_memory_research.py":
        "45ffdcf5544f62cf6252d172555f8aa3e980c2c79565f35b919c2ecadb15b070",
    "docs/experiments/p13-memory/corpus.json":
        "0d18fc55147d25bb1375df33fa6c456992d4d22c3bd7dfeed783fff22b44686a",
    "docs/experiments/p12-memory/cases.json":
        "a490bae7128f5480a1e8353511afbd36cec3fb2d881dc47410eaaa7837e39225",
    "docs/experiments/p12-memory/results.json":
        "0a47eb303fca76750d8daca27dceb16ebdce1ab206958fa28b24ae4209d3b055",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(store, key, text, when):
    event_id = "evt_research_" + key
    store.insert("events", event_id=event_id, schema_version=1, source="qq.onebot",
        source_event_id=event_id, conversation_id="conv_research", actor_id="owner",
        event_type="message.received", occurred_at=when, ingested_at=when,
        privacy_scope="private", body_json=encode({"text": text}), trace_id=event_id,
        memory_processed=1)
    return event_id


def seed(store, memory, corpus):
    if corpus["data_class"] != "PUBLIC_SYNTHETIC":
        raise ValueError("PUBLIC_SYNTHETIC_CORPUS_REQUIRED")
    store.insert("principals", actor_id="owner", kind="owner", created_at=NOW)
    store.insert("conversations", conversation_id="conv_research", platform="qq.onebot",
        platform_session_key="synthetic:FriendMessage:10001", scope="private",
        owner_actor_id="owner", created_at=NOW)
    sources = {row["id"]: row for row in corpus["sources"]}
    facts = list(corpus["facts"])
    for index in range(40):
        key = f"distractor_{index}"
        text = f"第{index}份合成储物档案的序号是存档{index:03}。"
        sources[key] = {"id": key, "day": "2026-10-21", "text": text}
        facts.append({"id": key, "source": key, "subject": "project", "content": text})
    identities = {}
    with store.transaction():
        for row in sources.values():
            source(store, row["id"], row["text"], row["day"] + "T04:00:00+00:00")
        for item in facts:
            evidence = sources[item["source"]]
            identities[item["id"]] = memory.add(Candidate(subject=item["subject"], kind="fact",
                fact_key=item["id"], content=item["content"],
                evidence_event_id="evt_research_" + item["source"], evidence_quote=item["content"]),
                evidence["day"] + "T04:00:00+00:00", confirmed=True)
    annotations = [{key: value for key, value in item.items() if key != "source"} | {
        "source_event_id": "evt_research_" + item["source"],
        "source_sha256": hashlib.sha256(sources[item["source"]]["text"].encode()).hexdigest(),
    } for item in corpus["alias_annotations"]]
    return identities, annotations


def erasure_trial(directory, cfg, store, memory, ledger, ids, annotations):
    corrected_text = "主人午餐通常吃番茄饭。"
    original = ids["lunch"]
    correction_source = source(store, "lunch_correction",
        f"/纠正这一条 {original} {corrected_text}", NOW)
    corrected = memory.decide(original, "correct", NOW, corrected_text,
                               evidence_event_id=correction_source)["memory_id"]
    correction = {}
    for method in METHODS:
        hits, _ = retrieve(memory, "午餐通常吃什么？", method, annotations, NOW)
        found = {row["memory_id"] for row in hits}
        correction[method] = {"new_value_recalled": corrected in found,
                              "superseded_hits": int(original in found)}
    archive, restored = directory / "before-forget.enc", directory / "isolated-restored.db"
    key = Fernet.generate_key()
    backup(cfg.database, archive, key)
    with store.transaction():
        ledger.expire_events(store, ["evt_research_project_alias"], NOW)
    memory.decide(corrected, "forget", NOW)
    restore(archive, restored, key, cfg.erasure_ledger)
    erased = {original, corrected, ids["project_alias"], ids["project_steps"]}
    reports = []
    reopened = Store(str(restored))
    try:
        for name, active in (("current", store), ("old_backup_latest_ledger", reopened)):
            active.execute("DELETE FROM memory_vectors")
            active.execute("DELETE FROM memories_fts")
            active.execute("INSERT INTO memories_fts(memory_id,content) SELECT memory_id,content "
                           "FROM memories WHERE status IN ('HYPOTHESIS','CONFIRMED')")
            rebuilt = Memory(active, cfg, ledger)
            pool, sources = evidence_pool(rebuilt, NOW)
            methods = {}
            for method in METHODS:
                hits = [row for query in ("RQ", "苇桥计划的顺序", "南瓜饭", "番茄饭")
                        for row in retrieve(rebuilt, query, method, annotations, NOW)[0]]
                control, _ = retrieve(rebuilt, "独立档案的装订线颜色", method, annotations, NOW)
                methods[method] = {
                    "erased_memory_hits": sum(row["memory_id"] in erased for row in hits),
                    "erased_content_hits": sum(any(word in row["content"] for word in
                        ("简称为RQ", "先整理目录再校对页码", "南瓜饭", "番茄饭")) for row in hits),
                    "independent_fact_recalled": ids["control"] in {
                        row["memory_id"] for row in control},
                }
            vector_count = active.one("SELECT COUNT(*) n FROM memory_vectors WHERE memory_id IN "
                "(SELECT value FROM json_each(?))", (encode(sorted(erased)),))["n"]
            invalid_sources = [item[0]["source_event_id"] for item in
                active_annotations(annotations, pool, sources)
                if item[0]["source_event_id"] == "evt_research_project_alias"]
            reports.append({"state": name, "methods": methods,
                "erased_vector_rows": vector_count,
                "erased_alias_source_still_active": bool(invalid_sources),
                "independent_fact_exists": active.one(
                    "SELECT COUNT(*) n FROM memories WHERE memory_id=? AND status='CONFIRMED'",
                    (ids["control"],))["n"] == 1})
    finally:
        reopened.close()
    assert all(not row["erased_vector_rows"] and not row["erased_alias_source_still_active"]
               and row["independent_fact_exists"] and all(
                   not item["erased_memory_hits"] and not item["erased_content_hits"]
                   and item["independent_fact_recalled"] for item in row["methods"].values())
               for row in reports)
    return correction, reports


def evaluate(directory, embedding_cache):
    from huggingface_hub import snapshot_download

    for relative, expected in FROZEN_FILES.items():
        if sha(ROOT / relative) != expected:
            raise ValueError("FROZEN_INPUT_OR_ALGORITHM_CHANGED")
    cached = snapshot_download(REPOSITORY, revision=REVISION, cache_dir=str(embedding_cache),
        allow_patterns=["*.json", "*.txt", "model_optimized.onnx"], local_files_only=True)
    if not (Path(cached) / "model_optimized.onnx").is_file():
        raise ValueError("PINNED_LOCAL_EMBEDDING_CACHE_REQUIRED")
    corpus = json.loads((DATA / "corpus.json").read_text("utf-8"))
    queries = json.loads((DATA / "queries.json").read_text("utf-8"))["queries"]
    cfg = Settings(owner_qq_id="10001", bot_qq_id="10002", bridge_token="synthetic-token-" * 4,
        database=str(directory / "synthetic.db"), erasure_ledger=str(directory / "erasures.jsonl"),
        memory_enabled=True, proactive_enabled=False, embedding_cache_dir=str(embedding_cache))
    migrate(cfg.database)
    store, ledger = Store(cfg.database), ErasureLedger(cfg.erasure_ledger)
    try:
        ledger.initialize(store)
        memory = Memory(store, cfg, ledger)
        ids, annotations = seed(store, memory, corpus)
        inverse = {value: key for key, value in ids.items()}
        pool, _ = evidence_pool(memory, NOW)
        # Warm the fixed native encoder/vector cache equally before timing all three methods.
        memory.index.scores("合成候选索引预热", pool)
        predictions = {method: [] for method in METHODS}
        for item in queries:
            for method in METHODS:
                started = perf_counter_ns()
                rows, additions = retrieve(memory, item["query"], method, annotations, NOW)
                elapsed = perf_counter_ns() - started
                predictions[method].append({"id": item["id"],
                    "retrieved": [inverse[row["memory_id"]] for row in rows],
                    "retrieved_utf8_bytes": sum(len(row["content"].encode()) for row in rows),
                    "local_retrieval_ms": elapsed / 1000000,
                    "additions": [{**addition, "memory_id": inverse[addition["memory_id"]]}
                                  for addition in additions]})
        # Gold labels were unavailable to all completed retrieval calls above.
        labels = json.loads((DATA / "gold.json").read_text("utf-8"))["labels"]
        gold = {row["id"]: row for row in labels}
        if len(gold) != len(labels) or set(gold) != {row["id"] for row in queries}:
            raise ValueError("HOLDOUT_LABEL_ID_MISMATCH")
        summaries = {}
        for method, cases in predictions.items():
            for row in cases:
                required = set(gold[row["id"]]["required"])
                allowed = set(gold[row["id"]]["allowed"])
                row.update(required=sorted(required),
                    missing=sorted(required - set(row["retrieved"])),
                    false_positive=sorted(set(row["retrieved"]) - allowed))
            required = sum(len(row["required"]) for row in cases)
            missing = sum(len(row["missing"]) for row in cases)
            returned = sum(len(row["retrieved"]) for row in cases)
            false = sum(len(row["false_positive"]) for row in cases)
            summaries[method] = {"queries": len(cases), "required_memories": required,
                "necessary_memories_recalled": required - missing, "missing": missing,
                "returned_candidates": returned, "false_positive_candidates": false,
                "required_recall": (required - missing) / required if required else None,
                "candidate_precision": (returned - false) / returned if returned else None,
                "candidate_utf8_bytes": sum(row["retrieved_utf8_bytes"] for row in cases),
                "mean_warm_local_retrieval_ms": sum(row["local_retrieval_ms"] for row in cases)
                / len(cases)}
        correction, erasure = erasure_trial(directory, cfg, store, memory, ledger, ids, annotations)
        return {"schema_version": 1, "evidence": "OFFLINE_SYNTHETIC_CANDIDATE_COMPARISON",
            "source_baseline_sha": "eb5143500bed5395bb45f2a2cc20335da9988491",
            "algorithm_corpus_freeze_commit": FREEZE_COMMIT,
            "frozen_sha256": FROZEN_FILES,
            "queries_sha256": sha(DATA / "queries.json"), "gold_sha256": sha(DATA / "gold.json"),
            "native_memory_sha256": sha(ROOT / "companion/memory.py"),
            "native_retrieval_sha256": sha(ROOT / "companion/retrieval.py"),
            "embedding": INDEX_VERSION, "semantic_min_score": cfg.semantic_min_score,
            "facts": len(ids), "candidate_limit": 6, "max_added_candidates": 2, "max_hops": 1,
            "annotation_semantics": "human_labelled_synthetic_quote_not_automatic_semantic_proof",
            "gold_loaded_after_all_retrieval": True,
            "previous_results_not_rerun": {"P12_holdout": "6/7", "P8_context": "7/8"},
            "summaries": summaries, "cases": predictions, "correction": correction,
            "source_erasure_old_backup_and_index_rebuild": erasure,
            "model_http_attempts": 0, "real_model_tokens": None, "real_model_cost": None,
            "real_business_memory_samples": None, "real_phone_samples": None,
            "real_natural_days": None, "production_retrieval_changed": False,
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
    scratch = ROOT / ".local/test-temp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p13-research-", dir=scratch) as directory:
        report = evaluate(Path(directory), args.embedding_cache.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(encode({"evidence": report["evidence"], "summaries": report["summaries"],
                  "erasure": report["source_erasure_old_backup_and_index_rebuild"]}))


if __name__ == "__main__":
    main()
