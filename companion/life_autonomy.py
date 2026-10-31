"""Read-only Life guidance from the exact, already authorized decision context."""


def enhance_decision_context(context):
    """Native Goal/Reader receipts own progress; this view has no state or clock."""
    guidance = []
    for item in context["self_interests"]:
        guidance.append({
            "trait_id": item["trait_id"],
            "topic": item["topic"],
            "status": item["status"],
            "independent_sessions": item["independent_sessions"],
            "independent_days": item["independent_days"],
            "source_event_ids": item["source_event_ids"],
            "action": "deepen" if item["status"] == "STABLE" else "continue",
            "reason": "依据已完成的独立阅读回执，后续探索仍须原生目标和读取许可。",
        })
    return {**context, "autonomy_guidance": {"depth_control": guidance}}
