"""Behavior regressions for the standalone synthetic contract, not Core tests."""

import copy
import unittest

from run import FixtureStore, assemble, benchmark, load_data


class OfflineContextRegressionTests(unittest.TestCase):
    def test_historical_miss_is_preserved_and_v2_retrieves_same_reference(self):
        result = benchmark()
        original = result["historical_original_run_not_reexecuted"]
        self.assertEqual(original["baseline_retention"]["missing"], ["pressure:tea"])
        self.assertEqual(original["experiment_retention"]["missing"], ["indirect:tea"])
        legacy = result["variants"]["legacy_rule_replay"]["legacy_subset"]
        current = result["variants"]["v2_contract"]["legacy_subset"]
        self.assertEqual(legacy["required_reference_retention"], {"present": 7, "total": 8})
        self.assertEqual(legacy["reference_failures"], ["indirect"])
        self.assertEqual(current["required_reference_retention"], {"present": 8, "total": 8})

    def test_all_prospective_positive_and_negative_contract_cases(self):
        result = benchmark()
        summary = result["variants"]["v2_contract"]["all_cases"]
        self.assertEqual(summary["case_count"], 30)
        self.assertEqual(summary["required_reference_retention"], {"present": 19, "total": 19})
        self.assertEqual(summary["reference_failures"], [])
        self.assertEqual(summary["forbidden_visible_count"], 0)
        self.assertEqual(summary["level_mismatches"], [])
        self.assertEqual(summary["assembly_violations"], [])

    def test_real_telemetry_remains_absent(self):
        result = benchmark()
        for key in (
            "actual_usage", "actual_input_tokens", "actual_output_tokens",
            "actual_cache_hit_tokens",
            "actual_cost", "reconciled_cost", "configured_real_rates", "currency", "tokenizer",
        ):
            self.assertIsNone(result[key])
        for key in ("provider_latency_ms", "phone_e2e_latency_ms"):
            self.assertEqual(result[key], {"n": 0, "p50": None, "p95": None})
        self.assertEqual(result["provider_http_requests"], 0)
        self.assertEqual(result["qq_send_attempts"], 0)
        self.assertFalse(result["embedding_model_executed"])

    def test_gold_label_mutation_cannot_change_assembled_context(self):
        _, _, memories, cases = load_data()
        case = next(row for row in cases if row["id"] == "quoted")
        changed = {
            **case, "id": "renamed_fixture", "required": ["fiction"],
            "review_select": ["forgotten"],
        }
        left, right = FixtureStore(memories, [], case), FixtureStore(memories, [], changed)
        try:
            self.assertEqual(assemble(left, case, "v2_contract"),
                             assemble(right, changed, "v2_contract"))
        finally:
            left.db.close()
            right.db.close()

    def test_arbitrary_indirect_query_with_candidate_does_not_fall_through_to_l0(self):
        _, _, memories, _ = load_data()
        for query in ("按惯例办？", "同以前一样？", "嗯，就那个。"):
            case = {"id": "held_out_rule_probe", "query": query, "candidates": ["tea"]}
            store = FixtureStore(memories, [], case)
            try:
                context = assemble(store, case, "v2_contract")
                self.assertEqual(context["level"], "L3")
                self.assertTrue(context["additional_review_proposed"])
                self.assertEqual(context["references"][0]["memory_id"], "tea")
            finally:
                store.db.close()

    def test_seed_and_final_reference_caps_under_overfull_candidates(self):
        _, _, memories, _ = load_data()
        expanded = copy.deepcopy(memories)
        candidates = []
        for i in range(12):
            key = f"seed-{i}"
            candidates.append(key)
            expanded[key] = {**memories["tea"], "memory_id": key}
        case = {"id": "many", "query": "还记得饮料吗？", "candidates": candidates}
        store = FixtureStore(expanded, [], case)
        try:
            context = assemble(store, case, "v2_contract")
            self.assertEqual(len(context["references"]), 6)
            self.assertEqual(context["level"], "L3")
        finally:
            store.db.close()

    def test_valid_second_edge_is_not_traversed_but_works_when_it_is_a_seed(self):
        _, extra, memories, cases = load_data()
        case = next(row for row in cases if row["id"] == "valid_second_hop_denied")
        store = FixtureStore(memories, extra["edges"], case)
        try:
            first = assemble(store, case, "v2_contract")
            self.assertEqual({row["memory_id"] for row in first["references"]},
                             {"chain_seed", "chain_mid"})
        finally:
            store.db.close()
        case = {**case, "candidates": ["chain_mid"]}
        store = FixtureStore(memories, extra["edges"], case)
        try:
            second = assemble(store, case, "v2_contract")
            self.assertIn("chain_end", {row["memory_id"] for row in second["references"]})
        finally:
            store.db.close()

    def test_removing_one_of_multiple_exact_dependencies_invalidates_derived_node(self):
        _, extra, memories, _ = load_data()
        case = {
            "id": "partial_support", "query": "还记得合成链吗？",
            "candidates": ["chain_mid"], "removed_evidence": ["synthetic_chain_b"],
        }
        store = FixtureStore(memories, extra["edges"], case)
        try:
            context = assemble(store, case, "v2_contract")
            self.assertEqual(context["references"], [])
            self.assertEqual(context["on_unresolved"], "clarify_or_state_unverified")
        finally:
            store.db.close()


if __name__ == "__main__":
    unittest.main()
