"""Read-only capability facts shared by the reply prompt and operator views.

READY describes local admission readiness, never a completed action or proof that
an upstream service is reachable. Actual workflows still enforce their leases,
source checks and budgets at dispatch. No guard that writes state is called here.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from companion import live_text, paid_budget, private_life, qq_images, video_input
from companion.budget import total_monthly
from companion.contracts import CoreError
from companion.exploration import METERED_READS_SQL
from companion.source_grants import SourceGrantService, SourceGrantSnapshot
from companion.sources import supports


def _meta(store, key):
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
    return row["value"] if row else None


def _observation(facts, now):
    if not isinstance(facts, dict):
        return "UNOBSERVED", {}
    try:
        observed = datetime.fromisoformat(facts["observed_at"])
        expires = datetime.fromisoformat(facts["expires_at"])
        current = datetime.fromisoformat(now)
        if observed.tzinfo is None or expires.tzinfo is None or not observed <= current:
            return "UNOBSERVED", {}
        if not observed < expires:
            return "UNOBSERVED", {}
        return ("OBSERVED", facts) if current < expires else ("STALE", {})
    except (KeyError, TypeError, ValueError):
        return "UNOBSERVED", {}


def _live_blocks(store, settings, now):
    approval = settings.live_text_window
    reasons = []
    if live_text.active(store, settings):
        if approval is None:
            reasons.append("LIVE_APPROVAL_REQUIRED")
        else:
            if not approval.live_approved:
                reasons.append("LIVE_APPROVAL_REQUIRED")
            if not approval.starts_at <= datetime.fromisoformat(now) < approval.ends_at:
                reasons.append("LIVE_APPROVAL_EXPIRED")
        if live_text.stopped(store, approval.plan_sha256 if approval else None):
            reasons.append("LIVE_WINDOW_STOPPED")
    if store.one("SELECT 1 FROM runtime_meta WHERE key IN "
                 "('model_budget_blocked','model_budget_restore_required')"):
        reasons.append("BUDGET_RECONCILIATION_REQUIRED")
    if store.one("SELECT 1 FROM model_usage WHERE state='UNKNOWN' LIMIT 1"):
        reasons.append("MODEL_USAGE_UNKNOWN")
    return reasons


def _model_blocks(store, settings, now):
    reasons = _live_blocks(store, settings, now)
    if settings.mode == "live":
        try:
            approval = paid_budget.contract(settings, now)
        except CoreError as error:
            reasons.append(error.code)
        else:
            reasons += _paid_capacity_blocks(store, settings, approval, now)
    else:
        reasons.append("DEMO_MODE")
    return reasons


def _periods(settings, now):
    local = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return tuple(at.astimezone(UTC).isoformat(timespec="microseconds")
                 for at in (day, day.replace(day=1)))


def _paid_capacity_blocks(store, settings, approval, now):
    """Report exhausted native quotas without estimating or reserving another call."""
    reasons = []
    if store.one(
        "SELECT 1 FROM model_usage m LEFT JOIN model_admissions a USING(request_id) "
        "WHERE a.request_id IS NULL AND m.state IN ('RESERVED','UNKNOWN') LIMIT 1",
    ):
        reasons.append("MODEL_LEGACY_USAGE_RECONCILIATION_REQUIRED")
    day, month = _periods(settings, now)
    daily = store.one(
        "SELECT COUNT(*) calls,COALESCE(SUM(billed_micro_usd),0) amount "
        "FROM model_usage WHERE occurred_at>=?", (day,),
    )
    unlimited = approval.unlimited_period_spend
    if (daily["calls"] >= approval.daily_calls
        or (not unlimited and daily["amount"] >= approval.daily_micro_usd)):
        reasons.append("MODEL_DAILY_BUDGET_EXHAUSTED")
    if not unlimited:
        monthly = total_monthly(store, month)
        if settings.pricing_known and monthly >= settings.monthly_limit_micro_usd:
            reasons.append("MONTHLY_BUDGET_EXHAUSTED")
        if monthly >= approval.monthly_micro_usd:
            reasons.append("MODEL_MONTHLY_BUDGET_EXHAUSTED")
    window = store.one(
        "SELECT COUNT(*) calls,COALESCE(SUM(m.billed_micro_usd),0) amount "
        "FROM model_admissions a JOIN model_usage m USING(request_id) WHERE a.window_id=?",
        (approval.window_id,),
    )
    if (window["calls"] >= approval.window_calls
        or (not unlimited and window["amount"] >= approval.window_micro_usd)):
        reasons.append("MODEL_WINDOW_BUDGET_EXHAUSTED")
    running = store.one(
        "SELECT COUNT(*) n FROM model_usage m JOIN model_admissions a USING(request_id) "
        "WHERE m.state IN ('RESERVED','UNKNOWN')",
    )["n"]
    if running >= approval.concurrency:
        reasons.append("MODEL_PROVIDER_CONCURRENCY_EXHAUSTED")
    return reasons


def _reflection_blocks(store, settings, now):
    day, month = _periods(settings, now)
    used = store.one(
        "SELECT COUNT(CASE WHEN occurred_at>=? THEN 1 END) calls,"
        "COALESCE(SUM(CASE WHEN occurred_at>=? THEN billed_micro_usd ELSE 0 END),0) daily,"
        "COALESCE(SUM(billed_micro_usd),0) monthly FROM model_usage "
        "WHERE scenario='reflection' AND occurred_at>=?", (day, day, month),
    )
    reasons = []
    if used["calls"] >= settings.reflection_max_daily_calls:
        reasons.append("SCENARIO_CALL_LIMIT")
    unlimited = bool(settings.mode == "live" and settings.paid_model
                     and settings.paid_model.unlimited_period_spend)
    if not unlimited:
        if (settings.reflection_daily_micro_usd > 0
            and used["daily"] >= settings.reflection_daily_micro_usd):
            reasons.append("DAILY_BUDGET_EXHAUSTED")
        if (settings.reflection_monthly_micro_usd > 0
            and used["monthly"] >= settings.reflection_monthly_micro_usd):
            reasons.append("SCENARIO_MONTHLY_BUDGET_EXHAUSTED")
    return reasons


def public_source_readiness(store, settings, clock, adapter_id, *, integration=None,
                            operation="read"):
    """Use native adapter/grant and reservation accounting without exposing custody."""
    now = clock()
    policy = settings.observation_adapters.get(adapter_id)
    observation, facts = _observation(integration, now)
    unavailable, paused = [], _live_blocks(store, settings, now)
    limits = {}
    source = policy.source if policy else None
    if not policy or not supports(source, "get"):
        unavailable.append("READER_NOT_CONFIGURED")
    else:
        if operation not in {"read", "discover"} or not supports(source, operation):
            unavailable.append("READER_OPERATION_UNAVAILABLE")
        limits = {
            "max_requests": policy.reader_max_requests,
            "max_response_bytes": settings.exploration_max_response_bytes,
            "daily_requests": settings.exploration_daily_requests,
            "monthly_requests": settings.exploration_monthly_requests,
            "daily_bytes": settings.exploration_daily_bytes,
            "daily_deep_reads": settings.exploration_daily_deep_reads,
        }
        if not settings.observation_enabled or not (
            settings.content_enabled or settings.exploration_enabled
        ):
            unavailable.append("READING_DISABLED")
        if source == "web" and (not settings.web_reading_enabled or not policy.allowed_hosts):
            unavailable.append("WEB_HOST_SCOPE_REQUIRED")
        if policy.source_grant_required:
            grants = store.rows(
                "SELECT r.grant_id,r.epoch,r.fingerprint,r.expires_at FROM source_grants g "
                "JOIN source_grant_revisions r USING(grant_id) WHERE g.adapter_id=? AND "
                "r.epoch=(SELECT MAX(v.epoch) FROM source_grant_revisions v "
                "WHERE v.grant_id=g.grant_id)", (adapter_id,),
            )
            valid, grant_reasons = False, []
            for grant in grants:
                try:
                    SourceGrantService(store, settings, clock).require(
                        adapter_id, SourceGrantSnapshot.model_validate(grant),
                    )
                    valid = True
                    break
                except CoreError as error:
                    grant_reasons.append(error.code)
            if not valid:
                unavailable.extend(grant_reasons or ["SOURCE_GRANT_REQUIRED"])
        day, month = _periods(settings, now)
        used = store.one(
            "SELECT SUM(accounted_requests) monthly, SUM(CASE WHEN started_at>=? "
            "THEN accounted_requests ELSE 0 END) daily, SUM(CASE WHEN started_at>=? "
            "THEN accounted_bytes ELSE 0 END) bytes, SUM(CASE WHEN started_at>=? "
            "AND operation='read' THEN 1 ELSE 0 END) reads "
            f"FROM ({METERED_READS_SQL}) WHERE started_at>=?", (day, day, day, month),
        )
        if ((used["daily"] or 0) + policy.reader_max_requests > limits["daily_requests"]
            or (used["monthly"] or 0) + policy.reader_max_requests > limits["monthly_requests"]
            or (used["bytes"] or 0) + limits["max_response_bytes"] > limits["daily_bytes"]
            or (operation == "read" and (used["reads"] or 0) >= limits["daily_deep_reads"])):
            paused.append("READ_BUDGET_UNAVAILABLE")
    if observation == "STALE":
        paused.append("READER_OBSERVATION_STALE")
    elif facts.get("accepting_tasks") is False:
        paused.append("READER_NOT_ACCEPTING_TASKS")
    return {
        "adapter_id": adapter_id, "source": source,
        "state": "UNAVAILABLE" if unavailable else "PAUSED" if paused else "READY",
        "reason_codes": list(dict.fromkeys(unavailable + paused)),
        "limits": limits, "runtime_observation": observation,
    }


def snapshot(store, settings, clock, *, model_tools=(), integrations=None):
    """Return a closed, credential-free projection; integration data is never echoed."""
    now, cfg = clock(), settings
    integrations = integrations if isinstance(integrations, dict) else {}
    readers = integrations.get("readers", {})
    readers = readers if isinstance(readers, dict) else {}
    rows = []
    live_blocks = _live_blocks(store, cfg, now)
    model_blocks = _model_blocks(store, cfg, now)
    life_pause = ["LIFE_PAUSED"] if _meta(store, "life_paused") == "1" else []

    def add(identifier, summary, invocation=("automatic",), *, enabled=True,
            unavailable=(), paused=(), limits=None, observation="UNOBSERVED"):
        unavailable = list(unavailable) + ([] if enabled else ["FEATURE_DISABLED"])
        rows.append({
            "id": identifier, "state": "UNAVAILABLE" if unavailable else
            "PAUSED" if paused else "READY", "invocation": list(invocation),
            "summary": summary, "reason_codes": list(dict.fromkeys([*unavailable, *paused])),
            "limits": limits or {}, "runtime_observation": observation,
        })

    add("memory.manage", "主人可查询、确认、纠正和忘记记忆。", ("owner_command",),
        enabled=cfg.memory_enabled, unavailable=["OWNER_COMMAND_SCOPE_REQUIRED"]
        if private_life.strict(store, cfg) else [])
    add("memory.recall", "对相关记忆做有来源的检索与审核。", enabled=cfg.memory_enabled,
        paused=model_blocks + ([] if cfg.memory_daily_micro_usd > 0
                               else ["SCENARIO_BUDGET_DISABLED"]),
        limits={"daily_micro_usd": cfg.memory_daily_micro_usd})
    for name, summary, extra, limits in (
        ("diary", "按已有对话来源整理日记。", ["DIARY_PAUSED"]
         if _meta(store, "diary_paused") == "1" else [],
         {"daily_micro_usd": cfg.diary_daily_micro_usd}),
        ("consolidation", "整理已有记忆和日常记录。", life_pause, {}),
        ("reflection", "基于已有来源提出反思建议。", life_pause,
         {"daily_micro_usd": cfg.reflection_daily_micro_usd,
          "monthly_micro_usd": cfg.reflection_monthly_micro_usd,
          "max_daily_calls": cfg.reflection_max_daily_calls}),
        ("wake", "在已有目标和限额内调度自主活动。", life_pause,
         {"max_daily_runs": cfg.wake_max_daily_runs}),
    ):
        paused = model_blocks + extra
        if limits and any(value <= 0 for value in limits.values()):
            paused += ["SCENARIO_BUDGET_DISABLED"]
        if name == "reflection":
            paused += _reflection_blocks(store, cfg, now)
        if name == "diary" and _meta(store, "owner_qq_id") != cfg.owner_qq_id:
            paused += ["OWNER_IDENTITY_REQUIRED"]
        if name == "wake" and not (cfg.goals_enabled and cfg.exploration_enabled):
            paused += ["GOALS_AND_EXPLORATION_REQUIRED"]
        add(name, summary, enabled=getattr(cfg, name + "_enabled"), paused=paused, limits=limits)
    for name, summary in (
        ("affect", "结合已有互动线索调整回应状态。"),
        ("expression", "在回复中使用已有表达偏好。"),
        ("expression_learning", "从已有互动中学习表达偏好。"),
    ):
        add(name, summary, enabled=getattr(cfg, name + "_enabled"), paused=live_blocks)
    add("goals", "主人可查看、接受和取消目标及承诺。", ("owner_command",),
        enabled=cfg.goals_enabled, paused=live_blocks, limits={"max_active": cfg.goals_max_active})
    from companion.social import proactive_allowed

    sharing = [] if proactive_allowed(store, cfg, now) else ["PROACTIVE_PAUSED_OR_QUIET"]
    for name, summary in (("proactive", "在主动交流限额内发起私聊。"),
                          ("social", "把已读取且适合分享的内容带入私聊。")):
        add(name, summary, enabled=getattr(cfg, name + "_enabled"),
            paused=model_blocks + sharing, limits={"daily_limit": cfg.proactive_daily_limit})

    readable, discoverable = [], []
    for adapter_id, policy in sorted(cfg.observation_adapters.items()):
        if not supports(policy.source, "get"):
            continue
        ready = public_source_readiness(store, cfg, clock, adapter_id,
                                        integration=readers.get(adapter_id))
        readable.append(ready)
        discoverable.append(public_source_readiness(
            store, cfg, clock, adapter_id, integration=readers.get(adapter_id),
            operation="discover",
        ))
        summaries = {
            "bilibili": "读取 B站元数据、可用字幕及有证据的部分评论和弹幕；覆盖以读取回执为准。",
            "heybox": "读取小黑盒文章正文、图片引用和有证据的部分评论；覆盖以读取回执为准。",
            "web": "读取已配置主机范围内的网页正文。",
        }
        rows.append({"id": "reading." + adapter_id, "invocation": ["automatic", "owner_command"],
                     "summary": summaries.get(policy.source, "读取已授权来源的内容。"),
                     **{key: value for key, value in ready.items()
                        if key not in {"adapter_id", "source"}}})
    for source, label in (("bilibili", "B站"), ("heybox", "小黑盒"), ("web", "其他网页")):
        if not any(row["source"] == source for row in readable):
            add("reading." + source, "读取" + label + "内容。",
                unavailable=["READER_NOT_CONFIGURED"])
    add("exploration", "按已有兴趣和目标发现可读内容。", enabled=cfg.exploration_enabled,
        unavailable=[] if any(row["state"] != "UNAVAILABLE" for row in discoverable)
        else ["READER_UNAVAILABLE"], paused=live_blocks + life_pause +
        ([] if any(row["state"] == "READY" for row in discoverable)
         else ["READ_BUDGET_UNAVAILABLE"]))
    add("bilibili.video", "B站视频画面分析尚未接入；字幕不代表看过完整视频。",
        unavailable=["VIDEO_FRAMES_NOT_CONNECTED"])

    image_errors = []
    try:
        if cfg.live_text_window is None:
            raise CoreError("LIVE_IMAGE_APPROVAL_REQUIRED", 403)
        qq_images.policy(cfg, cfg.live_text_window)
    except CoreError as error:
        image_errors.append(error.code)
    add("qq.images", "分析本次主人发送且已认证捕获的图片。", unavailable=image_errors,
        paused=model_blocks)
    video_errors = []
    try:
        video_input.contract(cfg, "capability-check", "capability-check")
    except CoreError as error:
        video_errors.append(error.code)
    names = {value for value in model_tools if isinstance(value, str)}
    names.update(value.get("function", {}).get("name") for value in model_tools
                 if isinstance(value, dict) and isinstance(value.get("function"), dict))
    add("qq.animation", "本次主人动图的完整动作分析由 content_vision 中的 understand_video 处理。",
        unavailable=video_errors + image_errors, paused=model_blocks)
    add("model.understand_video", "仅分析当前已捕获的主人动图；只在本轮实际提供工具时可调用。",
        ("model_callable",), unavailable=video_errors +
        ([] if "understand_video" in names else ["TOOL_NOT_EXPOSED_THIS_TURN"]),
        paused=model_blocks)
    add("unicode.emoji", "保留并理解消息中的 Unicode 表情原文。")
    add("qq.face_names", "识别 QQ 原生表情 ID 的名称。",
        enabled=cfg.rich_input_scope == "references_v1" or bool(cfg.live_text_window
                and cfg.live_text_window.rich_input_scope == "references_v1"))
    face_observation, faces = _observation(integrations.get("qq_faces"), now)
    face_paused = (["STATIC_FACE_IMAGES_PAUSED"] if faces.get("static_images") is False else [])
    if face_observation == "STALE":
        face_paused.append("STATIC_FACE_OBSERVATION_STALE")
    add("qq.face_images", "可接收认证捕获的 QQ 原生表情静态图；是否看过以本轮图像回执为准。",
        unavailable=image_errors, paused=model_blocks + face_paused, observation=face_observation)

    bridge_observation, bridge = _observation(integrations.get("bridge_memes"), now)
    private_errors = [] if private_life.enabled(cfg) else ["MEME_DISCOVERY_PRIVATE_SCOPE_REQUIRED"]
    for name, summary, flag, connected in (
        ("catalog", "系统可接收 Bridge 已审核候选；实际素材以 Bridge 回执为准。",
         cfg.expression_enabled, bridge.get("catalog_ready")),
        ("community_import", "系统可安排受限发现导入；实际来源配置和素材以 Bridge 回执为准。",
         cfg.meme_discovery_enabled, bridge.get("community_import")),
    ):
        paused = ["BRIDGE_CAPABILITY_PAUSED"] if connected is False else []
        if bridge_observation == "STALE":
            paused.append("BRIDGE_OBSERVATION_STALE")
        add("memes." + name, summary, enabled=flag, unavailable=private_errors,
            paused=model_blocks + paused, observation=bridge_observation)
    add("memes.search", "通过已配置的搜索服务发现表情资源。",
        enabled=cfg.meme_discovery_enabled, unavailable=private_errors +
        ([] if bridge.get("search_provider") == "brave" and bridge.get("search_ready") is True
         else ["SEARCH_PROVIDER_UNOBSERVED"]), paused=model_blocks,
        observation=bridge_observation)
    add("memes.semantic", "对已取得来源证明的表情样本做语义理解。",
        enabled=cfg.meme_semantics_enabled,
        unavailable=[] if cfg.paid_model and cfg.paid_model.meme_input
        and private_life.enabled(cfg) else ["PAID_MEME_APPROVAL_REQUIRED"],
        paused=model_blocks + ([] if cfg.meme_caption_daily_micro_usd > 0
                               else ["SCENARIO_BUDGET_DISABLED"]))
    for name in ("voice", "camera", "android"):
        adapter = cfg.observation_adapters.get(getattr(cfg.media, name + "_adapter"))
        add("media." + name, "主人可显式开启受限的" +
            {"voice": "语音", "camera": "摄像头", "android": "Android 观察"}[name] + "会话。",
            ("owner_command",), enabled=cfg.media.enabled and getattr(cfg, name + "_enabled"),
            unavailable=[] if cfg.observation_enabled and adapter and adapter.source == name
            else ["MEDIA_ADAPTER_NOT_CONFIGURED"], paused=live_blocks)
    add("executive", "主人可提交受独立审批约束的执行任务。", ("owner_command",),
        enabled=cfg.executive.enabled, unavailable=[] if cfg.executive.endpoints
        else ["EXECUTOR_NOT_CONFIGURED"], paused=live_blocks)
    return {"schema_version": 1, "capabilities": rows}


def prompt(summary, *, compact=False):
    """Render only the safe capability projection already used by the console."""
    labels = {"READY": "可用", "PAUSED": "暂缓", "UNAVAILABLE": "不可用"}
    routes = {"automatic": "系统流程", "owner_command": "主人指令", "model_callable": "本轮工具"}
    lines = ["\n当前能力事实：可用表示本地可接纳相应任务，不代表已经执行或远端在线。",
             "系统流程和主人指令不等于模型工具。只有标记为可用的本轮工具可直接调用。",
             "没有回执不要声称看过、听过或完成。B站字幕不代表看过完整视频；视频画面分析未接入。",
             "UNOBSERVED 只表示没有进程运行观测，不等于功能关闭；实际素材和覆盖以回执为准。"]
    tools = [row["id"] for row in summary["capabilities"]
             if row["state"] == "READY" and "model_callable" in row["invocation"]]
    lines.append("本轮可调用工具：" + ("、".join(tools) if tools else "无") + "。")
    if compact:
        # Keep the full state inventory without displacing current reading evidence.
        lines = ["\n能力状态是本地接纳条件，不是已执行或远端在线；以本轮回执确认结果。",
                 "系统流程和主人指令不等于模型工具；本轮工具："
                 + ("、".join(tools) if tools else "无") + "。",
                 "B站仅元数据/可用字幕/部分评论弹幕；字幕不代表看过完整视频。"
                 "小黑盒仅正文/图片引用/部分评论。图片、动图、表情以分析回执为准。"]
        for state, label in labels.items():
            names = [row["id"] for row in summary["capabilities"] if row["state"] == state]
            if names:
                lines.append(label + "：" + ",".join(names) + "。")
        lines.append("所有未观测的进程在线状态、素材和覆盖均待实际回执确认。")
        return "\n".join(lines)
    omitted = "部分能力未列出；未列出的能力不推定可用。"
    for row in summary["capabilities"]:
        line = f"- {row['id']}：{labels[row['state']]}"
        if row["state"] == "READY":
            line += f"（{'/'.join(routes[value] for value in row['invocation'])}）{row['summary']}"
        if row["reason_codes"]:
            line += "（" + row["reason_codes"][0] + "）"
        if row["state"] == "READY" and row["runtime_observation"] != "OBSERVED":
            line += " [" + row["runtime_observation"] + "]"
        if len(("\n".join([*lines, line, omitted])).encode("utf-8")) > 6000:
            lines.append(omitted)
            break
        lines.append(line)
    return "\n".join(lines)
