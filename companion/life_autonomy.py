"""Read-only Life guidance from the exact, already authorized decision context."""

# Options are directions she may take or ignore. None grants a read, a share or budget, and
# none is a target: resting or keeping a slow topic is always as valid as exploring.
OPTIONS = {
    "wait_for_view": "最近一次阅读还没有形成自己的评价，可以先不急着判断",
    "pivot": "几次读下来都不太喜欢，可以换个话题或先放下",
    "pause": "这次读完不太喜欢，但经历还少，不必急着下结论",
    "deepen": "几次读下来一直喜欢，可以找更深入的来源",
    "continue": "目前的看法还是暂时的，可以再读一个来源看看",
    "compare": "看法有分歧，可以找一个不同角度的来源再比较",
    "reflect": "看法刚因为最近一次阅读有了变化，可以写进私人日记，合适时也可以和主人聊",
    "rest": "可以暂时不探索这个话题",
}


def _options(item):
    view = item["view"]
    stance, settled = view["stance"], view["certainty"] == "settled"
    options = []
    if view["latest_unjudged"]:
        options.append("wait_for_view")
    if stance == "negative":
        options.append("pivot" if settled else "pause")
    elif stance == "positive":
        options.append("deepen" if settled and item["status"] == "STABLE" else "continue")
    elif stance == "mixed":
        options.append("compare")
    elif item["status"] != "FADING":
        options.append("continue")
    latest = view["latest"][-1]["event_id"] if view["latest"] else None
    if view["history"] and view["history"][-1]["because"]["event_id"] == latest:
        options.append("reflect")
    options.append("rest")
    return [{"action": key, "reason": OPTIONS[key]} for key in dict.fromkeys(options)]


def enhance_decision_context(context):
    """Native Goal/Reader receipts and her judgements own progress; no state or clock here."""
    guidance = []
    for item in context["self_interests"]:
        view = item["view"]
        guidance.append({
            "trait_id": item["trait_id"],
            "topic": item["topic"],
            "status": item["status"],
            "independent_sessions": item["independent_sessions"],
            "independent_days": item["independent_days"],
            "owner_suggested_sessions": item["owner_suggested_sessions"],
            "source_event_ids": item["source_event_ids"],
            "view": {"stance": view["stance"], "certainty": view["certainty"]},
            "why": view["latest"],
            "changes": view["history"][-3:],
            "options": _options(item),
        })
    return {**context, "autonomy_guidance": {"depth_control": guidance}}
