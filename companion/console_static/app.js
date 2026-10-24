"use strict";
/* Companion 主人控制台。
   只调用同源 /v1/console；写操作带 CSRF 与幂等键，结果不明时不换键重试。
   页面通过 DOM 构建（不拼接 HTML），样式只经 CSSOM 设置，满足 style-src 'self'。 */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function el(tag, attributes = {}, ...children) {
  const svg = ["svg", "path", "circle", "g", "line", "text"].includes(tag);
  const node = svg ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [key, value] of Object.entries(attributes || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "class") node.setAttribute("class", value);
    else if (key === "text") node.textContent = value;
    else if (key === "style" && typeof value === "object") for (const [name, v] of Object.entries(value)) node.style.setProperty(name, v);
    else if (["checked", "value", "disabled", "selected"].includes(key) && !svg) node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const PATHS = {
  home: "M4 11 12 4l8 7v9h-5v-6H9v6H4z", timeline: "M6 4v16M6 7h12M6 12h8M6 17h10",
  memory: "M12 3a6 6 0 0 0-3.5 10.9V17h7v-3.1A6 6 0 0 0 12 3zM9.5 20.5h5",
  diary: "M6 3h11a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM8 3v18M11 8h4M11 12h4",
  book: "M4 5c3-1.5 5.5-1.5 8 0v15c-2.5-1.5-5-1.5-8 0zM20 5c-3-1.5-5.5-1.5-8 0v15c2.5-1.5 5-1.5 8 0z",
  smile: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8.5 14.5c1.8 2 5.2 2 7 0M9 9.5h.01M15 9.5h.01",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21",
  qq: "M12 3a5 5 0 0 0-5 5v2l-2 4c1 .5 1.6.3 2 0 .3 2 1.3 3.4 2.6 4.3L8 20h8l-1.6-1.7c1.3-.9 2.3-2.3 2.6-4.3.4.3 1 .5 2 0l-2-4V8a5 5 0 0 0-5-5z",
  pulse: "M3 12h4l2.5-6 5 12 2.5-6H21",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19.4 13.5l1.6 1.2-2 3.4-1.9-.7a7 7 0 0 1-2 1.2L14.8 21h-4l-.3-2.4a7 7 0 0 1-2-1.2l-1.9.7-2-3.4 1.6-1.2a7 7 0 0 1 0-2.4L4.6 9.9l2-3.4 1.9.7a7 7 0 0 1 2-1.2L10.8 3h4l.3 2.4a7 7 0 0 1 2 1.2l1.9-.7 2 3.4-1.6 1.2a7 7 0 0 1 0 2.4z",
  toggle: "M8 7h8a5 5 0 0 1 0 10H8A5 5 0 0 1 8 7zM8 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  refresh: "M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4",
  logout: "M10 4H5v16h5M14 8l4 4-4 4M18 12H9", menu: "M4 7h16M4 12h16M4 17h16", close: "M6 6l12 12M18 6 6 18",
  check: "M5 12.5 10 17l9-10", warn: "M12 4 2.8 20h18.4zM12 10v4.5M12 17.5h.01", arrow: "M5 12h14M13 6l6 6-6 6",
  left: "M15 6l-6 6 6 6", right: "M9 6l6 6-6 6", edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3", send: "M4 12 20 4l-6 16-3-7z",
  coin: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM9.5 9.5h4a1.5 1.5 0 0 1 0 3h-3a1.5 1.5 0 0 0 0 3h4M12 7.5v1.5M12 16v1",
  pause: "M8 5v14M16 5v14", lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3", file: "M6 3h8l4 4v14H6zM14 3v4h4",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
};
function icon(name, cls = "i") {
  return el("svg", { class: cls, viewBox: "0 0 24 24", "aria-hidden": "true" }, el("path", { d: PATHS[name] || PATHS.spark }));
}

const ROUTES = [
  ["日常", [["overview", "今天", "home"], ["activity", "活动时间线", "timeline"], ["memories", "记忆", "memory"], ["life", "日记与成长", "diary"]]],
  ["内容", [["content", "阅读与文件", "book"], ["memes", "表情素材", "smile"], ["voice", "语音与节点", "mic"], ["account-actions", "QQ 资料行为", "qq"]]],
  ["管理", [["caps", "能力与开关", "toggle"], ["diagnostics", "运行诊断", "pulse"], ["configuration", "配置", "gear"]]],
];
const ROUTE_NAME = Object.fromEntries(ROUTES.flatMap(([, list]) => list.map(([key, name]) => [key, name])));

const STATUS = {
  READY: ["可用", "ok"], ATTENTION: ["需要留意", "warn"], ACTIVE: ["已启用", "run"], ENABLED: ["已启用", "ok"],
  DISABLED: ["未启用", "off"], UNAVAILABLE: ["不可用", "bad"], PAUSED: ["已暂停", "warn"], STOPPED: ["已停止", "off"],
  RECORDED: ["有运行记录", "ok"], STALE: ["记录已过期", "warn"], UNOBSERVED: ["尚无记录", "off"], OBSERVED: ["已观测", "ok"],
  QUEUED: ["排队中", "run"], PENDING: ["待处理", "run"], RUNNING: ["处理中", "run"], RETRY_WAIT: ["等待重试", "warn"],
  SUCCEEDED: ["已完成", "ok"], COMPLETED: ["已完成", "ok"], COMPLETE: ["已完成", "ok"], COMMITTED: ["已保存", "ok"],
  SETTLED: ["已结算", "ok"], SENT: ["已发送", "ok"], ACK: ["已送达", "ok"], UNKNOWN: ["结果不明", "warn"],
  FAILED: ["失败", "bad"], ERROR: ["发生错误", "bad"], CANCELLED: ["已取消", "off"], SUPERSEDED: ["已被新输入接续", "off"],
  RECALLED: ["已撤回", "off"], CONFIRMED: ["已确认", "ok"], HYPOTHESIS: ["待确认", "warn"], FORGOTTEN: ["已忘记", "off"],
  RETRACTED: ["已撤销", "off"], DELETED: ["已删除", "off"], REDACTED: ["已清除", "off"], EXPIRED: ["已过期", "off"],
  PROPOSED: ["待接受", "warn"], REVIEW: ["待审核", "warn"], QUARANTINED: ["已隔离", "bad"], INVALID: ["已失效", "bad"],
  INDEXED: ["已索引", "ok"], FETCHED: ["已读取", "ok"], DISCOVERED: ["已发现", "run"], WATCHED: ["已有分析", "ok"],
  PARTIAL: ["部分完成", "warn"], RESERVED: ["已预留", "run"], WAITING: ["等待中", "run"], NOT_CONFIGURED: ["未配置", "off"],
  DELIVERY_UNCERTAIN: ["投递结果不明", "warn"], BLOCKED: ["受阻", "warn"], DRAFT: ["草稿", "off"],
  ONLINE: ["近期有联系", "ok"], OFFLINE: ["联系已过期", "off"], BUSY: ["有未结束任务", "run"], TRANSCRIBED: ["已转写", "ok"],
  LEASED: ["已领取", "run"], SENDING: ["正在发送", "run"], UNVERIFIED: ["需要核对", "warn"], FRESH: ["近期已观测", "ok"],
  REVIEWED: ["审核有效", "ok"], REVOKED: ["已撤销", "off"], AVAILABLE: ["可执行", "ok"], REJECTED: ["未通过", "bad"],
  SKIPPED: ["本轮未改动", "off"], PLANNING: ["正在规划", "run"], MATCHED: ["与计划一致", "ok"], CHANGED: ["与计划不同", "warn"],
  ACCEPTED: ["已接受", "ok"], DONE: ["已完成", "ok"], SIGNED_OUT: ["已退出", "off"],
};
const statusOf = s => STATUS[String(s || "").toUpperCase()] || [s ? "其他状态" : "未记录", "off"];
function tag(s, label) { const [text, tone] = statusOf(s); return el("span", { class: "tag " + tone }, label || text); }

const ACTIVITY_TITLES = {
  "event.accepted": "收到你的一条消息", "console.login": "你登录了控制台", "console.operation": "控制台操作已记录",
  "configuration.saved": "保存了新的配置版本", "cloud_budget.bound": "建立了云端预算记录", "cloud_budget.configured": "更新了云端预算",
  "cloud_budget.native_settled": "同步了一笔已结算费用", "model.not_dispatched": "模型请求没有发出", "model.video_rate_correction": "修正了视频计费",
  "memory.conflict": "发现需要核对的记忆", "memory.control": "记忆管理操作已记录", "memory.recall_invalid": "一次记忆检索未通过核验",
  "reading.owner_requested": "收到你的阅读请求", "reply.superseded": "回复由新消息接续", "diary.recompute_requested": "请求重新整理日记",
  "diary.committed": "日记已保存", "diary.failed": "日记整理失败", "diary.cancelled": "日记整理已取消",
  "delivery.result": "消息投递有了结果", "delivery.owner_reconciled": "你核对了一条投递", "delivery.account_not_sent": "账号动作没有发出",
  "job.succeeded": "一项任务已完成", "poke.accepted": "收到拍一拍", "poke.completed": "拍一拍已完成", "poke.failed": "拍一拍失败",
  "goal.proposed": "她提出了一个目标", "goal.completed": "一个目标已完成", "goal.reflection_withdrawn": "一条反思已撤回",
  "affect.observed": "记录了一次情绪观察", "social.plan_started": "开始规划资料改动", "social.plan_decided": "资料改动计划已确定",
  "social.plan_failed": "资料改动计划失败", "social.action_reserved": "资料改动已预留额度", "social.action_dispatched": "资料改动已发出",
  "social.action_result": "资料改动有了平台回执", "social.avatar_reviewed": "头像素材已审核", "social.profile_observed": "观测了一次 QQ 资料",
  "social.runtime_cancelled": "资料行为已取消", "bili_video.scheduled": "安排了 B 站视频分析", "bili_video.unavailable": "B 站视频暂时无法分析",
  "bili_video.clips_declined": "视频片段未纳入分析", "source_grant.read_revoked": "停止了一项来源读取", "consent.granted": "授予了一项许可",
  "consent.revoked": "撤回了一项许可", "tool.proposed": "提出了一个工具任务", "tool.revoked": "撤回了一个工具任务",
  "tool.cancelled_before_dispatch": "工具任务在执行前取消", "live_text.handoff_aborted": "实时窗口交接已中止",
};
const activityTitle = item => ACTIVITY_TITLES[item.title] || item.title || "未命名记录";
const LANES = [
  ["chat", "对话", ["event.", "reply.", "delivery.", "poke.", "live_text.", "job."]],
  ["auto", "主动与自主", ["social.", "goal.", "tool.", "affect.", "wake.", "proactive."]],
  ["read", "阅读与内容", ["reading.", "bili_video.", "source_grant.", "consent.", "content."]],
  ["mind", "记忆与日记", ["memory.", "diary.", "consolidation.", "reflection."]],
  ["cost", "费用", ["cloud_budget.", "model."]],
  ["owner", "你的操作", ["console.", "configuration."]],
];
const laneOf = title => (LANES.find(([, , prefixes]) => prefixes.some(p => String(title).startsWith(p))) || ["owner"])[0];
const LANE_ICON = { chat: "send", auto: "spark", read: "book", mind: "memory", cost: "coin", owner: "gear" };

const CAP_NAMES = {
  "memory.manage": "记忆管理", "memory.recall": "记忆检索", diary: "日记", consolidation: "夜间整理", reflection: "反思",
  wake: "自主活动调度", affect: "情绪观察", expression: "表情回应", expression_learning: "表达习惯学习", goals: "目标与承诺",
  proactive: "主动私聊", social: "社交语义判断", "reading.bilibili": "B 站内容读取", "reading.heybox": "小黑盒内容读取",
  "reading.web": "网页阅读", exploration: "内容发现", "bilibili.video": "B 站视频分析", "qq.files": "私人文件",
  "qq.voice.asr": "语音转写", "qq.voice.tts": "语音回复", "qq.poke": "回应拍一拍", "qq.poke.native": "回拍一下",
  "qq.account.qzone_publish": "QQ 空间发布", "qq.account.nickname": "改昵称", "qq.account.signature": "改签名",
  "qq.account.avatar": "改头像", "qq.profile.observe": "资料观测", "qq.images": "私聊图片理解", "qq.animation": "私聊动图理解",
  "model.understand_video": "视频理解模型", "unicode.emoji": "Unicode 表情", "qq.face_names": "QQ 表情名称",
  "qq.face_images": "QQ 表情图像", "memes.catalog": "表情目录", "memes.community_import": "社区表情获取",
  "memes.search": "表情搜索", "memes.semantic": "表情语义理解", "media.voice": "网页语音会话", "media.camera": "摄像头",
  "media.android": "安卓设备", executive: "执行任务",
};
const CAP_GROUPS = [
  ["记忆与日记", ["memory.manage", "memory.recall", "diary", "consolidation", "reflection", "goals", "affect"]],
  ["主动与自主", ["proactive", "wake", "exploration", "social", "qq.poke", "qq.poke.native"]],
  ["阅读与内容", ["reading.web", "reading.bilibili", "reading.heybox", "bilibili.video", "model.understand_video", "qq.images", "qq.animation", "qq.files"]],
  ["表达与表情", ["expression", "expression_learning", "unicode.emoji", "qq.face_names", "qq.face_images", "memes.catalog", "memes.semantic", "memes.search", "memes.community_import"]],
  ["语音与媒体", ["qq.voice.asr", "qq.voice.tts", "media.voice", "media.camera", "media.android"]],
  ["QQ 资料与执行", ["qq.profile.observe", "qq.account.signature", "qq.account.nickname", "qq.account.avatar", "qq.account.qzone_publish", "executive"]],
];
/* 能力对应的配置项。只显示服务端实际返回的字段；executive 等部署保管的项不在其中。 */
const CAP_FIELDS = {
  "memory.manage": ["memory_enabled"], "memory.recall": ["semantic_min_score", "extraction_min_events", "extraction_idle_seconds", "emerging_min_sessions", "stable_min_days", "memory_daily_micro_usd", "raw_event_retention_days"],
  diary: ["diary_enabled", "diary_generate_hour", "diary_min_source_events", "diary_max_source_events", "diary_max_body_chars", "diary_max_output_tokens", "diary_catchup_days", "diary_max_attempts", "diary_daily_micro_usd"],
  consolidation: ["consolidation_enabled", "consolidation_local_hour", "consolidation_catchup_days"],
  reflection: ["reflection_enabled", "reflection_daily_max_suggestions", "reflection_min_sources", "reflection_max_daily_calls", "reflection_max_output_tokens", "reflection_daily_micro_usd", "reflection_monthly_micro_usd"],
  goals: ["goals_enabled", "goals_max_active", "goal_max_attempts"], affect: ["affect_enabled", "affect_decay_minutes", "affect_max_evidence"],
  proactive: ["proactive_enabled", "proactive_interval_seconds", "proactive_idle_seconds", "proactive_daily_limit", "quiet_start", "quiet_end"],
  wake: ["wake_enabled", "wake_min_interval_minutes", "wake_max_daily_runs", "autonomous_daily_micro_usd"],
  exploration: ["exploration_enabled", "exploration_daily_candidate_limit", "exploration_daily_deep_reads", "exploration_daily_requests", "exploration_monthly_requests", "exploration_max_response_bytes", "exploration_daily_bytes"],
  social: ["social_enabled", "social_semantic_threshold"], "qq.poke": ["poke_policy"], "qq.poke.native": ["poke_policy"],
  "reading.web": ["web_reading_enabled", "content_enabled"], "reading.bilibili": ["observation_enabled", "content_enabled"], "reading.heybox": ["observation_enabled", "content_enabled"],
  "bilibili.video": ["bili_video"], "model.understand_video": ["bili_video", "video_model_key"], "qq.images": ["content"], "qq.animation": ["content"], "qq.files": ["private_files"],
  expression: ["expression_enabled", "expression_cooldown_minutes", "expression_selection_probability", "expression_meme_categories"],
  expression_learning: ["expression_learning_enabled"], "unicode.emoji": ["expression_enabled"], "qq.face_names": ["expression_enabled"], "qq.face_images": ["expression_enabled"],
  "memes.catalog": ["meme_discovery_enabled"], "memes.search": ["meme_discovery_enabled", "meme_discovery_daily_queries"],
  "memes.community_import": ["meme_discovery_enabled", "meme_discovery_daily_download_bytes", "meme_discovery_daily_storage_bytes"],
  "memes.semantic": ["meme_semantics_enabled", "meme_caption_daily_micro_usd", "meme_caption_max_output_tokens"],
  "qq.voice.asr": ["voice_enabled", "voice"], "qq.voice.tts": ["voice_enabled", "voice"], "media.voice": ["voice_enabled"], "media.camera": ["camera_enabled"], "media.android": ["android_enabled"],
  "qq.profile.observe": ["social_autonomy"], "qq.account.signature": ["social_autonomy"], "qq.account.nickname": ["social_autonomy"], "qq.account.avatar": ["social_autonomy"], "qq.account.qzone_publish": ["social_autonomy"],
};
/* 可以立即暂停的能力对应 /mode 的 target */
const CAP_RUNTIME = { diary: "diary", wake: "life", proactive: "proactive" };
const REASONS = {
  FEATURE_DISABLED: "配置里没有开启", DEMO_MODE: "当前是演示模式", PAUSED: "运行时已暂停", DIARY_PAUSED: "日记已暂停", LIFE_PAUSED: "自主活动已暂停",
  PROACTIVE_PAUSED_OR_QUIET: "主动私聊已暂停或处于免打扰", CONTENT_DISABLED: "内容读取没有开启", READING_DISABLED: "阅读没有开启",
  DAILY_BUDGET_EXHAUSTED: "今日额度已用完", MONTHLY_BUDGET_EXHAUSTED: "本月额度已用完", MODEL_DAILY_BUDGET_EXHAUSTED: "模型今日额度已用完",
  MODEL_MONTHLY_BUDGET_EXHAUSTED: "模型本月额度已用完", SCENARIO_BUDGET_DISABLED: "这个场景没有分配额度", SCENARIO_CALL_LIMIT: "已到调用次数上限",
  BUDGET_RECONCILIATION_REQUIRED: "费用需要先核对", MODEL_USAGE_UNKNOWN: "有结果不明的模型调用", DELIVERY_RECONCILIATION_REQUIRED: "有投递需要先核对",
  READER_NOT_CONFIGURED: "读取程序没有配置", READER_UNAVAILABLE: "读取程序不可用", READER_OBSERVATION_STALE: "读取程序很久没有联系",
  WEB_HOST_SCOPE_REQUIRED: "没有设置允许读取的网站", SOURCE_GRANT_REQUIRED: "需要你授予读取授权", LIVE_APPROVAL_REQUIRED: "需要当前窗口的授权",
  LIVE_APPROVAL_EXPIRED: "窗口授权已过期", VOICE_SCOPE_REQUIRED: "语音需要授权范围", VOICE_NODE_SCOPE_REQUIRED: "语音节点需要授权范围",
  VOICE_CONFIG_INVALID: "语音配置无效", SOCIAL_AUTONOMY_DISABLED: "QQ 资料行为没有开启", SOCIAL_LIVE_SCOPE_REQUIRED: "需要当前窗口的授权",
  SOCIAL_REVIEWED_AVATAR_REQUIRED: "需要审核过的头像素材", EXECUTOR_NOT_CONFIGURED: "没有配置执行端", MEDIA_ADAPTER_NOT_CONFIGURED: "没有配置媒体服务",
  PINNED_PARSER_IMAGE_REQUIRED: "需要固定版本的解析镜像", PRIVATE_FILE_ADAPTER_UNAVAILABLE: "文件解析通道不可用", OWNER_IDENTITY_REQUIRED: "需要确认主人身份",
  POKE_COOLDOWN: "拍一拍冷却中", POKE_RATE_LIMIT: "拍一拍已到次数上限", GOALS_AND_EXPLORATION_REQUIRED: "需要先开启目标与内容发现",
  TOOL_NOT_EXPOSED_THIS_TURN: "这轮对话没有开放", STATIC_FACE_IMAGES_PAUSED: "QQ 表情图像已暂停",
};

/* ================= 中文标签：原因码、限额、对象配置子项 ================= */
Object.assign(REASONS, {
  BILI_VIDEO_CONCURRENCY_LIMIT: "B 站视频分析已到并发上限", BILI_VIDEO_READER_PAUSED: "B 站读取已暂停", BILI_VIDEO_READER_UNAVAILABLE: "B 站读取不可用",
  BILI_VIDEO_RECONCILIATION_REQUIRED: "B 站视频费用需要核对", BILI_VIDEO_SCOPE_REQUIRED: "B 站视频需要授权范围", BRIDGE_CAPABILITY_PAUSED: "AstrBot 这项能力已暂停",
  BRIDGE_OBSERVATION_STALE: "AstrBot 很久没有上报状态", CUMULATIVE_ACCOUNT_NOT_ENROLLED: "账号没有加入累计额度", CUMULATIVE_PAUSED: "累计额度已暂停",
  CUMULATIVE_POLICY_EXPIRED: "累计额度策略已过期", CUMULATIVE_SCOPE_REQUIRED: "累计额度需要授权范围", LIVE_IMAGE_APPROVAL_REQUIRED: "图片需要当前窗口的授权",
  LIVE_LINK_SCOPE_REQUIRED: "链接需要当前窗口的授权", LIVE_WINDOW_STOPPED: "私人窗口已停止", MEME_DISCOVERY_PRIVATE_SCOPE_REQUIRED: "表情获取需要私人授权范围",
  MODEL_LEGACY_USAGE_RECONCILIATION_REQUIRED: "历史模型费用需要核对", MODEL_PROVIDER_CONCURRENCY_EXHAUSTED: "模型服务并发已满", MODEL_USAGE_UNRECONCILED: "模型费用还没核对",
  MODEL_WINDOW_BUDGET_EXHAUSTED: "窗口额度已用完", OWNER_COMMAND_SCOPE_REQUIRED: "需要你的指令授权", PAID_IMAGE_APPROVAL_REQUIRED: "付费图片理解需要授权",
  PAID_MEME_APPROVAL_REQUIRED: "付费表情理解需要授权", PRIVATE_FILE_CONFIG_INVALID: "私人文件配置无效", PRIVATE_FILE_SCOPE_REQUIRED: "私人文件需要授权范围",
  PRIVATE_FILE_TRAFFIC_EXHAUSTED: "私人文件今日流量已用完", PRIVATE_POKE_NATIVE_DISABLED: "原生拍一拍没有开启", PRIVATE_POKE_SCOPE_REQUIRED: "拍一拍需要授权范围",
  QUARANTINED: "已隔离", READER_NOT_ACCEPTING_TASKS: "读取程序暂不接收任务", READER_OPERATION_UNAVAILABLE: "读取程序不支持这项操作",
  READ_BUDGET_UNAVAILABLE: "读取额度不可用", SCENARIO_MONTHLY_BUDGET_EXHAUSTED: "这个场景本月额度已用完", SEARCH_PROVIDER_UNOBSERVED: "搜索服务还没有上报状态",
  STATIC_FACE_OBSERVATION_STALE: "QQ 表情图像很久没有更新", VOICE_ASR_SCOPE_REQUIRED: "语音转写需要授权范围", VOICE_CAPACITY_EXCEEDED: "语音任务已满",
  VOICE_RESOURCE_RECONCILIATION_REQUIRED: "语音资源需要核对", STALE: "记录已过期", UNKNOWN: "结果不明", UNOBSERVED: "还没有观测", UNAVAILABLE: "不可用",
  BRIDGE_TIMEOUT: "连接 AstrBot 超时", MODEL_TIMEOUT: "模型请求超时", NETWORK_ERROR: "网络错误",
});
/* 未收录的原因码按词尾给出中文，界面上不出现英文代码 */
const REASON_TAILS = [["_SCOPE_REQUIRED", "需要授权范围"], ["_APPROVAL_REQUIRED", "需要授权"], ["_RECONCILIATION_REQUIRED", "需要先核对"], ["_NOT_CONFIGURED", "没有配置"],
  ["_DISABLED", "没有开启"], ["_PAUSED", "已暂停"], ["_UNAVAILABLE", "不可用"], ["_EXHAUSTED", "额度已用完"], ["_STALE", "很久没有联系"], ["_EXPIRED", "已过期"],
  ["_INVALID", "配置无效"], ["_LIMIT", "已到上限"], ["_COOLDOWN", "冷却中"], ["_TIMEOUT", "超时"], ["_FAILED", "失败"], ["_REQUIRED", "缺少前置条件"]];
function reasonText(code) {
  if (!code) return "";
  if (REASONS[code]) return REASONS[code];
  const tail = REASON_TAILS.find(([t]) => String(code).endsWith(t));
  return tail ? tail[1] : "未归类的原因";
}
const LIMIT_NAMES = {
  daily_micro_usd: "每日额度", monthly_micro_usd: "每月额度", daily_calls: "每日调用", daily_limit: "每日次数", cooldown_seconds: "冷却", mode: "模式",
  max_daily_images: "每日图片", max_daily_files: "每日文件", daily_requests: "每日请求", monthly_requests: "每月请求", daily_bytes: "每日流量",
  max_concurrent: "并发", deadline_seconds: "处理时限", max_chars: "字数上限", wait_seconds: "等待", daily_queries: "每日搜索", max_active: "同时进行",
};
const VALUE_NAMES = { auto: "自动", voice_input: "只回应语音", text_only: "只回文字", poke_only: "只拍回去", none: "不使用", qq_social_v1: "QQ 资料与空间", references_v1: "引用与转发", owner_private_v1: "仅主人私聊", isolated_container: "隔离容器", sentence: "按句子长度分段", semantic: "按语义分段（由她写的换行决定）", unlimited: "不限",
  happy: "开心", like: "喜欢", surprised: "惊讶", morning: "早安", sleep: "晚安", sad: "难过", angry: "生气", shy: "害羞", tired: "困了", thinking: "思考", love: "爱心", cry: "哭哭" };
function limitValue(k, v) {
  if (v === null || v === undefined) return "未设置";
  if (typeof v === "boolean") return v ? "开" : "关";
  if (typeof v === "string") return VALUE_NAMES[v] || v;
  if (/micro_usd/.test(k)) return money(v, "usd");
  if (/micro_cny/.test(k)) return yuan(v);
  if (/_seconds$/.test(k)) return duration(v);
  if (/_bytes$/.test(k)) return bytes(v);
  return count(v);
}
const limitName = k => LIMIT_NAMES[k] || subLabel(k);

/* 对象配置的子项名称 */
const SUB = {
  enabled: "开启", mode: "模式", scope: "范围", url: "地址", model: "模型", key: "密钥", protocol: "协议", nodes: "节点", reply: "语音回复", asr: "语音转写",
  proactive: "主动消息也用语音", media: "媒体处理", sampling: "抽样", limits: "资源限制", vision: "图片理解", adapter_id: "适配器", parser_image: "解析镜像",
  parser_socket: "解析通道", public_origin: "控制台地址", session_seconds: "会话时长", max_sessions: "最多同时登录", allow_native: "允许原生拍一拍",
  coalesce_seconds: "合并间隔", cooldown_seconds: "冷却", daily_limit: "每天最多", max_coalesced: "最多合并", max_text_chars: "文字上限", max_pending_seconds: "最长等待",
  max_links_per_event: "每条消息最多链接", max_images_per_event: "每条消息最多图片", max_images_per_article: "每篇文章最多图片", article_image_micro_usd: "文章图片单价",
  use_chat_model_for_vision: "用对话模型理解图片", max_image_bytes: "单张图片上限", max_daily_image_bytes: "每日图片流量", max_daily_images: "每日最多图片",
  image_daily_micro_usd: "图片理解每日额度", wait_seconds: "回复前最多等待", upload_window_seconds: "上传窗口", max_request_micro_usd: "单次请求上限",
  max_files_per_event: "每条消息最多文件", max_daily_files: "每日最多文件", max_daily_bytes: "每日流量", upload_lease_seconds: "上传租约", worker_lease_seconds: "处理租约",
  spool_retention_seconds: "暂存保留", max_file_bytes: "单个文件上限", max_expanded_bytes: "解压后上限", max_zip_entries: "压缩包最多条目", max_compression_ratio: "最大压缩比",
  max_pages: "最多页数", max_records: "最多条数", max_text_chars_file: "文字上限", max_table_depth: "表格最深层级", max_pdf_stream_bytes: "PDF 单流上限",
  cpu_seconds: "CPU 时间", wall_seconds: "总时长", memory_bytes: "内存上限", scratch_bytes: "临时空间", max_processes: "最多进程",
  signing_private_key: "签名私钥", job_ttl_seconds: "任务有效期", lease_ttl_seconds: "租约有效期", result_ttl_seconds: "结果有效期", max_pending_jobs: "排队上限",
  max_private_blob_bytes: "私有数据上限", max_chars: "字数上限", max_context_chars: "带入对话的字数", authorization_sha256: "授权摘要", asset_policy_sha256: "素材策略摘要",
  policy_version: "策略版本", qzone_enabled: "允许 QQ 空间发布", nickname_enabled: "允许改昵称", signature_enabled: "允许改签名", avatar_enabled: "允许改头像",
  qzone_daily_limit: "QQ 空间每天最多", profile_daily_limit: "资料每天最多改", qzone_cooldown_seconds: "QQ 空间冷却", profile_cooldown_seconds: "资料改动冷却",
  model_daily_calls: "模型每日调用", model_daily_micro_usd: "模型每日额度", decision_interval_seconds: "决策间隔", max_publish_chars: "发布字数上限",
  max_nickname_chars: "昵称字数上限", max_signature_chars: "签名字数上限", max_source_refs: "最多引用来源", max_source_chars: "来源字数上限", max_input_bytes: "输入上限",
  max_output_tokens: "输出上限", timeout_seconds: "超时", plan_ttl_seconds: "计划有效期", profile_baseline_seconds: "资料观测有效期", max_pending_plans: "最多待执行计划",
  max_asset_bytes: "素材大小上限", max_asset_dimension: "素材边长上限", deadline_seconds: "处理时限", max_concurrent: "并发", max_preview_frames: "最多预览帧",
  max_clips: "最多片段", max_clip_seconds: "单段时长", max_total_clip_seconds: "片段总时长", max_total_output_bytes: "输出总上限", max_width: "最大宽度", max_height: "最大高度",
  max_source_pixels: "源视频像素上限", max_source_seconds: "源视频时长上限", max_decoded_frames: "最多解码帧", max_output_frames: "最多输出帧", output_fps: "输出帧率",
  timestamp_tolerance_seconds: "时间戳容差", max_frame_gap_seconds: "最大帧间隔", min_frame_gap_seconds: "最小帧间隔", terminate_grace_seconds: "终止宽限",
  max_process_output_bytes: "进程输出上限", max_memory_bytes: "内存上限", initial_frames: "初始帧数", max_segments: "最多分段", max_segment_seconds: "单段时长",
  max_total_segment_seconds: "分段总时长", max_subtitles: "最多字幕", max_danmaku: "最多弹幕", subtitle_similarity: "字幕相似度", subtitle_duplicate_seconds: "字幕去重间隔",
  danmaku_duplicate_seconds: "弹幕去重间隔", danmaku_bin_seconds: "弹幕统计间隔", min_danmaku_support: "弹幕最少支持数", min_density_ratio: "最小密度比",
  local_window_seconds: "局部窗口", context_before_seconds: "之前的上下文", context_after_seconds: "之后的上下文", baseline_floor_per_second: "每秒基线",
  focus_terms: "关注词", max_evidence_per_selection: "每次选取最多证据", max_duration_seconds: "最长时长", provider_timeout_seconds: "服务商超时", image_adapter: "图片适配器",
  max_segments_reply: "最多分段", min_chars: "最少字数", interval_ms: "间隔（毫秒）",
};
const TOKENS = { max: "最多", min: "最少", daily: "每日", monthly: "每月", total: "总", seconds: "时长", bytes: "大小", chars: "字数", enabled: "开启", limit: "上限" };
const subLabel = k => SUB[k] || String(k).split("_").map(t => TOKENS[t] || "").join("") || "其他设置";
/* 与 poke_protocol / voice_config / social_autonomy_protocol 中的 Literal 保持一致 */
const SUB_ENUMS = { mode: { poke_policy: ["auto", "text_only", "poke_only", "none"], reply: ["voice_input", "auto"], segmented_reply: ["sentence", "semantic"] }, scope: { social_autonomy: ["none", "qq_social_v1"] } };
const isSensitiveSub = k => /(_sha256|private_key|_socket|parser_image|token|secret)$/.test(k) || k === "key";

/* ================= 状态与接口 ================= */
const SECRET_KEY = /password|cookie|authorization|api_key|access_token|refresh_token|csrf|secret|credential/i;
const state = {
  session: null, epoch: 0, generation: 0, route: "overview", sub: "", data: null, controller: null,
  tz: undefined, updatedAt: null, toastTimer: null, searchTimer: null,
  actKind: "all", memTab: "review", memQ: "", diaryDay: null, capQ: "", capF: "all", capCollapsed: new Set(),
  capMobile: false, contentF: "all", draft: new Map(), configSave: null, budgetDraft: new Map(), budgetSave: null,
  deliverySaves: new Map(), cfgQ: "", currency: "CNY", fx: 8000000, actFrom: "", actTo: "", memView: "list", gSel: null, voiceTab: "in",
};

class ApiError extends Error {
  constructor(code, status = 0, fields = [], uncertain = false) {
    super(code); this.code = code; this.status = status; this.fields = fields; this.uncertain = uncertain;
  }
}
const api = {
  attempts: new Map(),
  async request(path, { method = "GET", body, signal, authenticate = true, validate } = {}) {
    const mutation = method !== "GET";
    const headers = { Accept: "application/json" };
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timeout = setTimeout(abort, 15000);
    let attempt;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    try {
      if (mutation) {
        if (!globalThis.crypto?.randomUUID || !globalThis.crypto?.subtle) throw new ApiError("CONSOLE_HTTPS_REQUIRED");
        // 同一内容的重复提交沿用同一个幂等键，结果不明时不会变成第二次操作。
        const bytes = new TextEncoder().encode(JSON.stringify([method, path, body]));
        const hash = await crypto.subtle.digest("SHA-256", bytes);
        attempt = [...new Uint8Array(hash)].map(v => v.toString(16).padStart(2, "0")).join("");
        if (!this.attempts.has(attempt)) this.attempts.set(attempt, crypto.randomUUID());
        headers["Idempotency-Key"] = this.attempts.get(attempt);
        if (state.session) headers["X-CSRF-Token"] = state.session.csrf_token;
      }
      const response = await fetch("/v1/console" + path, {
        method, headers, credentials: "same-origin", cache: "no-store", redirect: "error",
        signal: controller.signal, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      let value = {};
      if (response.status !== 204) {
        try { value = await response.json(); }
        catch { throw new ApiError("CONSOLE_RESPONSE_INVALID", response.status, [], mutation); }
      }
      if (!response.ok) {
        if (response.status === 401 && authenticate) clearSession("会话已过期，请重新登录。");
        throw new ApiError(typeof value?.error === "string" ? value.error : "CONSOLE_REQUEST_FAILED",
          response.status, Array.isArray(value?.fields) ? value.fields : []);
      }
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiError("CONSOLE_RESPONSE_INVALID", response.status, [], mutation);
      if (validate && !validate(value)) throw new ApiError("CONSOLE_RESPONSE_INVALID", response.status, [], mutation);
      if (attempt) this.attempts.delete(attempt);
      return value;
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      if (error instanceof ApiError) throw error;
      throw new ApiError(controller.signal.aborted ? "CONSOLE_TIMEOUT" : "CONSOLE_NETWORK_ERROR", 0, [], mutation);
    } finally { clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
  },
  get(path, signal) { return this.request(path, { signal }); },
  write(path, body, method = "POST", validate) { return this.request(path, { method, body, validate }); },
};

function errorText(error) {
  const messages = {
    CONSOLE_NETWORK_ERROR: "连接中断了，请检查网络后重试。", CONSOLE_TIMEOUT: "服务器响应超时，请稍后重试。",
    CONSOLE_RESPONSE_INVALID: "服务器返回的数据不完整，请刷新核对。", CONSOLE_HTTPS_REQUIRED: "请通过 HTTPS 打开控制台后再操作。",
    CONSOLE_LOGIN_RATE_LIMIT: "密码错误次数过多，请 5 分钟后再试。", PROACTIVE_DISABLED_IN_CONFIG: "主动私聊在配置里是关闭的，先在配置里开启。",
    DIARY_NOT_FOUND: "这一天没有日记。", IDEMPOTENCY_CONFLICT: "同一个操作编号对应了不同内容，请刷新后重试。",
  };
  let message = messages[error.code];
  if (!message && error.status === 401) message = "密码不对，或者会话已过期，请重新登录。";
  if (!message && error.status === 403) message = "这个操作没有通过校验，请刷新页面后重试。";
  if (!message && error.status === 409) message = "记录或配置已经变化，请刷新后重新核对。";
  if (!message && [400, 422].includes(error.status)) message = "有填写内容不符合要求，请检查后重试。";
  if (!message && error.status === 429) message = "操作太频繁或额度不足，请稍后再试。";
  if (!message && error.status === 503) message = "服务暂时不可用，请到运行诊断里查看。";
  if (!message) message = "操作没有完成，请稍后重试。";
  if (error.uncertain) message = "操作结果还没确认，请先刷新核对。再次提交相同内容会沿用原来的操作编号。";
  return message + "（" + (typeof error.code === "string" ? error.code : "CONSOLE_REQUEST_FAILED") + "）";
}

/* ================= 格式 ================= */
const valid = v => typeof v === "string" && Number.isFinite(Date.parse(v));
function fmt(value, options) {
  if (!valid(value)) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", { hour12: false, timeZone: state.tz, ...options }).format(new Date(value));
}
const fmtTime = v => fmt(v, { hour: "2-digit", minute: "2-digit" });
const fmtDate = v => fmt(v, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const localDay = v => valid(v) ? new Intl.DateTimeFormat("en-CA", { timeZone: state.tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(v)) : "";
function localMinutes(v) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: state.tz, hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(v));
  const get = t => Number(parts.find(p => p.type === t)?.value || 0);
  return (get("hour") % 24) * 60 + get("minute");
}
const hm = m => String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
const count = v => typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("zh-CN") : "—";
const yuan = v => typeof v === "number" && Number.isFinite(v) ? "¥" + (v / 1e6).toFixed(2) : "—";
/* 模型账本按美元记账；显示时可以按配置汇率换算成人民币 */
function money(microUsd, unit = "usd") {
  if (typeof microUsd !== "number" || !Number.isFinite(microUsd)) return "—";
  if (unit !== "usd") return yuan(microUsd);
  if (state.currency === "CNY") { const cny = microUsd * state.fx / 1e6; return "¥" + (cny / 1e6).toFixed(cny % 10000 ? 4 : 2); }
  return "$" + (microUsd / 1e6).toFixed(microUsd % 10000 ? 4 : 2);
}
const usd = v => money(v, "usd");
function duration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "未记录";
  if (seconds >= 86400 && seconds % 86400 === 0) return seconds / 86400 + " 天";
  if (seconds >= 3600 && seconds % 3600 === 0) return seconds / 3600 + " 小时";
  if (seconds >= 60 && seconds % 60 === 0) return seconds / 60 + " 分钟";
  return seconds.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + " 秒";
}
function bytes(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return "未记录";
  return v >= 1048576 ? (v / 1048576).toFixed(1) + " MB" : v >= 1024 ? (v / 1024).toFixed(1) + " KB" : v + " 字节";
}
function safeUrl(value) {
  if (typeof value !== "string") return null;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
function safeValue(value, depth = 0) {
  if (depth > 4) return "…";
  if (value === null || value === undefined) return "未记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value !== "object") return String(value).slice(0, 8000);
  if (Array.isArray(value)) return value.slice(0, 30).map(v => safeValue(v, depth + 1)).join("、");
  return Object.entries(value).filter(([k]) => !SECRET_KEY.test(k)).slice(0, 30).map(([k, v]) => k + "：" + safeValue(v, depth + 1)).join("\n");
}
const shortId = v => String(v || "").slice(-8);

/* ================= 通用组件 ================= */
function head(title, desc, actions) {
  return el("div", { class: "page-head" }, el("div", {}, el("h1", {}, title), desc ? el("p", {}, desc) : null),
    actions ? el("div", { class: "actions" }, actions) : null);
}
function sheet(title, body, link, cls = "") {
  return el("section", { class: "sheet " + cls }, title ? el("div", { class: "sheet-head" }, el("h2", {}, title), link || null) : null,
    el("div", { class: "sheet-body" }, body));
}
const linkTo = (route, text) => el("a", { class: "link", href: "#" + route }, text, icon("arrow"));
function btn(text, onclick, { cls = "", symbol, type = "button", disabled = false, label } = {}) {
  return el("button", { type, class: "btn " + cls, onclick, disabled, "aria-label": label }, symbol ? icon(symbol) : null, text);
}
function empty(title, text, retry) {
  return el("div", { class: "sheet empty", role: "status" }, el("h3", {}, title), el("p", {}, text),
    retry ? btn("重新加载", () => loadRoute(), { symbol: "refresh" }) : null);
}
function kv(label, value) { return el("div", { class: "kv" }, el("span", {}, label), value instanceof Node ? value : el("span", {}, value)); }
function toast(text) {
  clearTimeout(state.toastTimer);
  const t = $("#toast"); t.replaceChildren(icon("check"), document.createTextNode(text)); t.hidden = false;
  state.toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
}
function notice(text, retry = false) {
  const n = $("#notice"); n.hidden = !text; n.replaceChildren();
  if (text) n.append(icon("warn"), el("span", {}, text), retry ? btn("重试", () => loadRoute(), { cls: "sm" }) : null);
}
function setConnection(kind, text) { const c = $("#conn"); c.className = "conn " + kind; c.lastElementChild.textContent = text; }

function openDialog(title, content, { wide = false } = {}) {
  const d = $("#dlg");
  d.classList.toggle("wide", wide);
  d.replaceChildren(el("div", { class: "dlg-head" }, el("h2", {}, title),
    el("button", { class: "icon-btn", type: "button", "aria-label": "关闭", onclick: () => d.close() }, icon("close"))),
    el("div", { class: "dlg-body" }, content));
  if (!d.open) d.showModal();
  requestAnimationFrame(() => $("input:not([type=hidden]), textarea, select", d)?.focus());
}
const closeDialog = () => $("#dlg").open && $("#dlg").close();
function formError(form, text) {
  let field = $(".form-error", form);
  if (!field) { field = el("p", { class: "form-error", role: "alert" }); form.append(field); }
  field.textContent = text; field.hidden = !text;
}
async function perform(form, button, operation, success) {
  const label = button.textContent, epoch = state.epoch;
  button.disabled = true; button.textContent = "正在处理…"; formError(form, "");
  try {
    const result = await operation();
    if (epoch !== state.epoch || !state.session) return;
    await success(result);
  } catch (error) {
    if (epoch === state.epoch && state.session) { if (form.isConnected) formError(form, errorText(error)); else notice(errorText(error)); }
  } finally { if (button.isConnected) { button.disabled = false; button.textContent = label; } }
}
function drawer(title, sub, body) {
  const d = $("#drawer");
  d.replaceChildren(el("div", { class: "drawer-head" }, el("div", {}, el("h2", {}, title), sub ? el("p", { class: "note" }, sub) : null),
    el("button", { class: "icon-btn", type: "button", "aria-label": "关闭", onclick: closeDrawer }, icon("close"))),
    el("div", { class: "drawer-body" }, body));
  $("#drawer-scrim").hidden = false; d.classList.add("open"); d.setAttribute("aria-hidden", "false");
  setTimeout(() => $(".drawer-head .icon-btn", d)?.focus(), 30);
}
function closeDrawer() { const d = $("#drawer"); d.classList.remove("open"); d.setAttribute("aria-hidden", "true"); $("#drawer-scrim").hidden = true; }
function detailList(detail, skip = []) {
  return Object.entries(detail || {}).filter(([k, v]) => !skip.includes(k) && !SECRET_KEY.test(k) && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => {
      const url = /url|source_page/.test(k) ? safeUrl(v) : null;
      const value = url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, String(v)) :
        /_bytes$/.test(k) ? bytes(v) : /_seconds$/.test(k) ? duration(v) : /_at$/.test(k) && valid(v) ? fmtDate(v)
        : typeof v === "string" && /^[A-Z][A-Z_]+$/.test(v) ? (STATUS[v] ? statusOf(v)[0] : reasonText(v)) : safeValue(v);
      return kv(DETAIL_LABELS[k] || subLabel(k), value);
    });
}
const DETAIL_LABELS = {
  entity_id: "关联对象", trace_id: "追踪编号", subject: "主体", memory_kind: "类型", needs_review: "需要核对", evidence_ids: "来源消息",
  local_date: "日期", version: "版本", commitment_state: "承诺状态", updated_at: "更新时间", owner_event_id: "你的消息", output_event_id: "输出记录",
  read_request_id: "读取请求", usage_request_id: "用量记录", reserved_bytes: "预留空间", asset_sha256: "素材摘要", source_sha256: "来源摘要",
  state: "状态", completed_at: "完成时间", count: "次数", billed_micro_usd: "已计费用", action_type: "投递类型",
  last_seen_at: "最近联系", resource_state: "上次资源状态", active_job_id: "关联任务", node_id: "处理节点", capabilities: "已配置能力",
  operation: "处理类型", task_state: "处理状态", partial: "音频不完整", input_bytes: "输入大小", input_duration_seconds: "输入时长",
  result_bytes: "结果大小", delivery_state: "投递状态", message_id: "消息编号", deadline_at: "处理截止", result_expires_at: "结果有效期",
  manifest_state: "配置核验", plan_state: "计划状态", receipt_state: "平台回执", dispatched_at: "开始发送", expires_at: "有效期至",
  revoked_at: "撤销时间", license_label: "使用许可", usage_conditions: "使用条件", purpose: "用途", dimensions: "尺寸", size_bytes: "大小",
  field: "资料项目", baseline_value: "最近观测内容", observed_at: "观测时间", freshness_seconds: "观测有效时长", freshness_state: "观测新鲜度",
  last_action_at: "最近相关操作", source_page: "来源页面", source_url: "来源链接", state_label: "状态说明", last_error_code: "最近原因",
};

/* ================= 会话、导航与读取 ================= */
const LOADERS = {
  overview: async signal => {
    const [o, a, d, c] = await Promise.allSettled([api.get("/overview", signal), api.get("/activity?limit=60", signal),
      api.get("/diagnostics", signal), api.get("/configuration", signal)]);
    if (o.status !== "fulfilled") throw o.reason;
    return { ...o.value, recent: a.status === "fulfilled" ? a.value : null, diagnostics: d.status === "fulfilled" ? d.value : null,
      configuration: c.status === "fulfilled" ? c.value : null };
  },
  activity: async (signal, more) => {
    // 按日期筛选时继续往前翻页，直到越过开始日期（最多 20 页）。
    let page = await api.get("/activity?limit=60" + (more ? "&before=" + encodeURIComponent(more) : ""), signal);
    const items = [...(page.items || [])];
    for (let n = 0; state.actFrom && page.next_cursor && n < 20; n++) {
      const oldest = items.at(-1); if (oldest && localDay(oldest.occurred_at) < state.actFrom) break;
      page = await api.get("/activity?limit=60&before=" + encodeURIComponent(page.next_cursor), signal);
      items.push(...(page.items || []));
    }
    return { ...page, items };
  },
  memories: signal => api.get("/memories?limit=60&review=" + (state.memTab === "review") + (state.memQ.trim() ? "&q=" + encodeURIComponent(state.memQ.trim()) : ""), signal),
  life: signal => api.get("/life", signal), content: signal => api.get("/content", signal), memes: signal => api.get("/memes", signal),
  voice: signal => api.get("/voice", signal), "account-actions": signal => api.get("/account-actions", signal),
  diagnostics: signal => api.get("/diagnostics", signal),
  caps: async signal => {
    const [o, c] = await Promise.all([api.get("/overview", signal), api.get("/configuration", signal)]);
    return { ...o, configuration: c };
  },
  configuration: async signal => { const c = await api.get("/configuration", signal); const fx = c.cloud_budget?.config?.usd_to_cny_micro; if (Number.isSafeInteger(fx) && fx > 0) state.fx = fx; return { configuration: c }; },
};
const PAGES = {};
const DRAFT_ROUTES = ["caps", "configuration"];

function clearSession(message = "") {
  state.epoch += 1; state.generation += 1; state.controller?.abort();
  state.session = null; state.data = null; state.draft.clear(); state.budgetDraft.clear(); state.deliverySaves.clear();
  state.memQ = ""; state.capQ = ""; state.cfgQ = ""; api.attempts.clear(); clearTimeout(state.searchTimer);
  closeDialog(); closeDrawer();
  $("#page").replaceChildren(); $("#notice").hidden = true; $("#toast").hidden = true;
  $("#app").hidden = true; $("#boot").hidden = true; $("#login").hidden = false;
  $("#pw").value = ""; $("#login-err").textContent = message; $("#login-err").hidden = !message;
}
function acceptSession(value) {
  if (typeof value.csrf_token !== "string" || !value.csrf_token || !valid(value.expires_at)) throw new ApiError("CONSOLE_RESPONSE_INVALID");
  state.session = { csrf_token: value.csrf_token, expires_at: value.expires_at };
  $("#login").hidden = true; $("#boot").hidden = true; $("#app").hidden = false; $("#pw").value = "";
  updateSessionLeft();
  return navigate();
}
function updateSessionLeft() {
  if (!state.session) return;
  const left = Math.max(0, Math.round((Date.parse(state.session.expires_at) - Date.now()) / 60000));
  $("#session-left").textContent = left > 0 ? "剩余 " + left + " 分钟" : "即将过期";
}

function renderNav() {
  const o = state.nav || {};
  $("#nav").replaceChildren(...ROUTES.flatMap(([group, list]) => [el("p", { class: "nav-group" }, group), ...list.map(([key, name, ic]) => {
    const badge = key === "memories" ? o.review : key === "diagnostics" ? o.problems : 0;
    return el("a", { href: "#" + key, "aria-current": state.route === key ? "page" : null }, icon(ic), el("span", {}, name),
      badge ? el("span", { class: "badge" + (key === "diagnostics" ? " bad" : "") }, String(badge)) : null);
  })]));
  const p = $("#presence"), paused = o.paused === true;
  p.classList.toggle("paused", paused);
  p.replaceChildren(el("span", { class: "orb", "aria-hidden": "true" }),
    el("strong", {}, o.mode === "demo" ? "演示模式" : o.ready === false ? "需要留意" : "正式运行"),
    el("span", {}, o.worker ? (statusOf(o.worker)[0] === "有运行记录" ? "工作进程有运行记录" : "工作进程：" + statusOf(o.worker)[0]) : "正在读取状态"),
    o.note ? el("p", {}, o.note) : null);
}
function updateNavFrom(overview) {
  if (!overview?.runtime) return;
  const caps = overview.capabilities?.capabilities || [];
  const paused = ["diary", "wake", "proactive"].every(id => caps.find(c => c.id === id)?.state !== "READY");
  state.nav = {
    review: overview.counts?.review_memories || 0, mode: overview.runtime.mode, worker: overview.runtime.worker?.state,
    ready: overview.runtime.state === "READY", paused,
    problems: (overview.counts?.unknown_calls || 0) + (state.nav?.uncertain || 0),
    note: overview.runtime.state === "READY" ? "运行状态正常。" : "有些状态需要留意，打开运行诊断查看。",
  };
  if (overview.budget?.timezone) state.tz = overview.budget.timezone;
  if (Number.isSafeInteger(overview.budget?.config?.usd_to_cny_micro) && overview.budget.config.usd_to_cny_micro > 0) state.fx = overview.budget.config.usd_to_cny_micro;
}

async function navigate() {
  const [route, sub = ""] = (location.hash.slice(1) || "overview").split("/");
  if (!ROUTE_NAME[route]) { location.hash = "overview"; return; }
  if (route !== state.route && state.draft.size && !DRAFT_ROUTES.includes(route) && !confirm("配置还有没保存的修改。要放弃修改并离开吗？")) {
    history.replaceState(null, "", "#" + state.route); return;
  }
  if (route !== state.route) {
    if (!DRAFT_ROUTES.includes(route)) state.draft.clear();
    state.data = null; state.capMobile = false; closeDrawer();
  }
  const same = route === state.route && state.data;
  state.route = route; state.sub = decodeURIComponent(sub);
  document.title = ROUTE_NAME[route] + " · Companion 主人控制台";
  $("#crumb").replaceChildren(el("b", {}, ROUTE_NAME[route]));
  $("#side").classList.remove("open");
  renderNav();
  if (!state.session) return;
  if (same) { render({ keepScroll: true }); return; }
  await loadRoute({ reset: true });
}

async function loadRoute({ reset = false, more = false } = {}) {
  if (!state.session) return;
  if (!reset && state.draft.size && DRAFT_ROUTES.includes(state.route) && !confirm("刷新会丢弃没保存的配置修改，继续吗？")) return;
  if (!reset) state.draft.clear();
  const generation = ++state.generation, epoch = state.epoch, route = state.route;
  state.controller?.abort(); state.controller = new AbortController();
  const signal = state.controller.signal;
  if (!state.data) $("#page").replaceChildren(el("div", { class: "loading", role: "status" }, el("span", { class: "spin" }), "正在读取…"));
  $("#page").setAttribute("aria-busy", "true"); $("#refresh").disabled = true; setConnection("loading", "正在同步"); notice("");
  try {
    let value = await LOADERS[route](signal, more ? state.data?.next_cursor : undefined);
    if (generation !== state.generation || epoch !== state.epoch || !state.session) return;
    if (value.items !== undefined && (!Array.isArray(value.items) || value.items.some(i => !i || !["string", "number"].includes(typeof i.id) || typeof i.status !== "string"))) {
      throw new ApiError("CONSOLE_RESPONSE_INVALID");
    }
    if (more && state.data?.items) {
      const unique = new Map(state.data.items.map(i => [String(i.id), i]));
      for (const i of value.items) unique.set(String(i.id), i);
      value = { ...value, items: [...unique.values()] };
    }
    if (route === "overview" || route === "caps") updateNavFrom(value);
    if (route === "overview" && value.diagnostics?.items) state.nav.uncertain = value.diagnostics.items.filter(i => i.status === "DELIVERY_UNCERTAIN").length;
    state.data = value; state.updatedAt = new Date().toISOString();
    renderNav(); render({ keepScroll: more });
    setConnection("", "已连接");
  } catch (error) {
    if (error.name === "AbortError" || generation !== state.generation || epoch !== state.epoch || !state.session) return;
    setConnection("off", navigator.onLine ? "同步失败" : "网络已断开");
    if (state.data) notice("数据没有更新。" + errorText(error), true);
    else $("#page").replaceChildren(empty(navigator.onLine ? "暂时读不到数据" : "网络已断开", errorText(error), true));
  } finally {
    if (generation === state.generation) { $("#page").setAttribute("aria-busy", "false"); $("#refresh").disabled = false; }
  }
}

function render({ keepScroll = false } = {}) {
  if (!state.session || !state.data) return;
  const y = scrollY, active = document.activeElement, focusId = active?.id, caret = active?.selectionStart;
  const focusKey = active?.dataset?.k;
  const body = PAGES[state.route](state.data), bar = saveBar();
  $("#page").replaceChildren(body, el("footer", { class: "foot-note" }, el("span", {}, "Companion Core 主人控制台"),
    el("span", {}, "本页读取于 " + fmtTime(state.updatedAt))), ...(bar ? [bar] : []));
  $("#page").classList.toggle("has-savebar", !!$("#page .savebar"));
  PAGES[state.route].after?.();
  if (keepScroll) window.scrollTo(0, y); else window.scrollTo(0, 0);
  const target = focusId ? document.getElementById(focusId) : focusKey ? $(`[data-k="${CSS.escape(focusKey)}"]`) : null;
  if (target && target !== document.body) { target.focus({ preventScroll: true }); if (caret != null && target.setSelectionRange) try { target.setSelectionRange(caret, caret); } catch {} }
}
const rerender = () => render({ keepScroll: true });

/* ================= 今天 ================= */
const tipOf = new WeakMap();
function cfgValue(configuration, key) { return configuration?.fields?.find(f => f.key === key)?.value; }
PAGES.overview = data => {
  const now = data.observed_at || new Date().toISOString(), today = data.budget?.local_day || localDay(now);
  const nowMin = localMinutes(now), hour = Math.floor(nowMin / 60);
  const greet = hour < 6 ? "夜深了" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
  const dateText = fmt(now, { month: "long", day: "numeric", weekday: "long" });
  const items = (data.recent?.items || []).filter(i => valid(i.occurred_at) && localDay(i.occurred_at) === today);
  const chats = items.filter(i => i.title === "event.accepted").length;
  return el("div", {},
    head(`${greet}，今天是 ${dateText}`, data.recent ? `今天收到你 ${chats} 条消息，记录了 ${items.length} 件事。` : "活动记录暂时没读到，其他状态照常显示。",
      btn("管理主动行为", openModeDialog, { symbol: "pause" })),
    dayStrip(items, nowMin, data), todoList(data),
    el("div", { class: "grid-2" },
      el("div", { class: "stack" }, budgetCard(data.budget, data.counts), recentCard(data.recent)),
      el("div", { class: "stack" }, capsCard(data.capabilities?.capabilities || []))));
};
function dayStrip(items, nowMin, data) {
  const pct = m => (m / 1440 * 100).toFixed(3) + "%";
  const qs = cfgValue(data.configuration, "quiet_start"), qe = cfgValue(data.configuration, "quiet_end");
  const bands = Number.isInteger(qs) && Number.isInteger(qe) ? (qs > qe ? [[qs, 24], [0, qe]] : qs < qe ? [[qs, qe]] : []) : [];
  let d = 0;
  const lanes = LANES.map(([key, name]) => {
    const own = items.filter(i => laneOf(i.title) === key).map(i => ({ m: localMinutes(i.occurred_at), i })).sort((a, b) => a.m - b.m);
    const groups = own.reduce((gs, e) => { const g = gs.at(-1); if (g && e.m - g.at(-1).m <= 20) g.push(e); else gs.push([e]); return gs; }, []);
    const marks = groups.map(g => {
      const first = g[0], last = g.at(-1), bad = g.some(e => ["DELIVERY_UNCERTAIN", "FAILED", "UNKNOWN"].includes(e.i.status) || /failed|unavailable|invalid|conflict/.test(e.i.title));
      const mark = el("button", { type: "button", class: "mark " + key + (bad ? " bad" : "") + (g.length > 1 ? " group" : ""),
        "aria-label": g.map(e => hm(e.m) + " " + activityTitle(e.i)).join("；"),
        style: { left: pct(first.m), "--d": String(d++), ...(g.length > 1 ? { width: `calc(${((last.m - first.m) / 1440 * 100).toFixed(3)}% + 6px)` } : {}) },
        onclick: () => { location.hash = "activity"; } }, g.length > 1 ? el("b", {}, String(g.length)) : null);
      tipOf.set(mark, g.map(e => [hm(e.m), activityTitle(e.i), statusOf(e.i.status)[0]]));
      return mark;
    });
    return [el("div", { class: "lane-label" }, name), el("div", { class: "lane" }, marks)];
  });
  const truncated = data.recent?.next_cursor && items.length >= (data.recent.items || []).length;
  return el("section", { class: "day enter", "aria-label": "今天发生的事" },
    el("div", { class: "day-head" }, el("h2", {}, "今天发生的事"),
      el("span", { class: "sum" }, "悬停或聚焦色块查看详情，现在是 ", el("b", {}, hm(nowMin)))),
    el("div", { class: "day-scroll" }, el("div", { class: "day-grid" }, lanes, el("div"),
      el("div", { class: "ruler" }, [0, 3, 6, 9, 12, 15, 18, 21, 24].map(h => el("span", { style: { left: (h / 24 * 100) + "%" } }, String(h).padStart(2, "0")))),
      el("div", { class: "day-overlay" },
        bands.map(([a, z], i) => el("div", { class: "quiet", style: { left: pct(a * 60), width: pct((z - a) * 60) } }, i === 0 ? el("span", {}, "免打扰") : null)),
        el("div", { class: "now", style: { left: pct(nowMin) } }, el("b", {}, "现在 " + hm(nowMin))),
        el("div", { class: "future", style: { left: pct(nowMin) } })))),
    !items.length ? el("p", { class: "note day-empty" }, data.recent ? "今天还没有记录。" : "活动记录暂时读不到。") : null,
    truncated ? el("p", { class: "note day-empty" }, "只显示了最近 60 条记录，更早的在活动时间线里。") : null);
}
PAGES.overview.after = () => { const day = $(".day"); const sc = $(".day-scroll");
  if (sc && innerWidth <= 860 && sc.scrollWidth > sc.clientWidth) sc.scrollLeft = sc.scrollWidth * .7 - sc.clientWidth * .7;
  setTimeout(() => day?.classList.remove("enter"), 1600); };
function todoList(data) {
  const out = [], counts = data.counts || {}, b = data.budget || {};
  const uncertain = (data.diagnostics?.items || []).filter(i => i.status === "DELIVERY_UNCERTAIN");
  if (uncertain.length) out.push(["bad", uncertain.length, "条投递结果不明", "不会自动重发，请到 QQ 里看看是否已出现", "去核对", "diagnostics"]);
  if (counts.review_memories) out.push(["", counts.review_memories, "条记忆等你确认", "确认后她才会在对话里用到", "去确认", "memories"]);
  if (counts.unknown_calls) out.push(["", counts.unknown_calls, "次模型调用结果不明", "相关费用会一直占用额度，直到核对完成", "查看诊断", "diagnostics"]);
  if (b.reconciliation_required || b.restore_reconciliation_required) out.push(["bad", "!", "费用记录需要核对", b.admission_blocked ? "云调用已暂停，需核对独立账本" : "请核对独立费用记录", "查看诊断", "diagnostics"]);
  if (b.fx_expired) out.push(["bad", "!", "汇率已过期", "过期后不会发起新的云调用", "更新汇率", "configuration/budget"]);
  if (data.runtime?.worker?.state && data.runtime.worker.state !== "RECORDED") out.push(["info", "!", "聊天处理进程没有近期记录", "进程可能没有启动或已经停止", "查看诊断", "diagnostics"]);
  if (!out.length) return el("div", { class: "todo-clear" }, icon("check"), "没有需要你处理的事。");
  return el("div", { class: "todo", "aria-label": "等你处理" }, out.map(([tone, n, title, sub, action, route]) =>
    el("div", { class: "todo-item " + tone }, el("span", { class: "n" }, String(n)), el("h3", {}, title), el("p", {}, sub),
      el("a", { class: "btn sm", href: "#" + route }, action))));
}
function budgetCard(b, counts = {}) {
  if (!b || b.currency !== "CNY") return sheet("今日云端费用", el("p", { class: "note" }, "费用记录还没有人民币口径。"));
  const limit = b.daily_limit_micro_cny, pct = v => limit > 0 ? Math.min(100, Math.max(0, v / limit * 100)) + "%" : "0%";
  const reconcile = b.reconciliation_required || b.restore_reconciliation_required;
  return sheet("今日云端费用", [
    reconcile ? el("div", { class: "alert bad" }, icon("warn"), el("span", {}, (b.restore_reconciliation_required ? "备份恢复后的费用记录可能不完整" : "费用记录需要核对") + (b.admission_blocked ? "，云调用已暂停。" : "。"))) : null,
    b.unknown_micro_cny > 0 ? el("div", { class: "alert" }, icon("warn"), el("span", {}, "有结果不明的费用，它们会一直占用额度，直到核对完成。")) : null,
    el("div", { class: "money-big" }, el("b", { class: "num" }, yuan(b.remaining_micro_cny)), el("span", {}, "今天还能用，限额 " + yuan(limit))),
    el("div", { class: "bar", role: "img", "aria-label": `已结算 ${yuan(b.settled_micro_cny)}，已预留 ${yuan(b.reserved_micro_cny)}，结果不明 ${yuan(b.unknown_micro_cny)}` },
      el("i", { class: "s", style: { width: pct(b.settled_micro_cny) } }), el("i", { class: "r", style: { width: pct(b.reserved_micro_cny) } }), el("i", { class: "u", style: { width: pct(b.unknown_micro_cny) } })),
    el("div", { class: "legend" }, [["已结算", b.settled_micro_cny, "var(--ink)"], ["已预留", b.reserved_micro_cny, "#8b90c4"], ["结果不明", b.unknown_micro_cny, "var(--amber)"]]
      .map(([n, v, c]) => el("div", { style: { "--c": c } }, n, el("b", {}, yuan(v))))),
    b.over_limit ? el("p", { class: "form-error" }, "总占用已超出限额，之后的预留会受限。") : null,
    el("div", { class: "queues" }, el("div", {}, el("b", {}, count(counts.pending_jobs)), el("span", {}, "排队中的任务")),
      el("div", {}, el("b", {}, count(counts.pending_deliveries)), el("span", {}, "待发送消息")),
      el("div", {}, el("b", {}, count(counts.review_memories)), el("span", {}, "待确认记忆"))),
    el("p", { class: "note mt" }, "按配置费率和汇率估算，不等于服务商的最终账单。")], linkTo("configuration/budget", "调整预算"));
}
function recentCard(recent) {
  if (!recent) return sheet("最近发生", el("p", { class: "note" }, "活动记录暂时读不到，可以稍后刷新。"));
  const items = (recent.items || []).slice(0, 6);
  return sheet("最近发生", items.length ? el("ul", { class: "rows recent" }, items.map(i => el("li", {},
    el("span", { class: "ic" }, icon(LANE_ICON[laneOf(i.title)])), el("span", { class: "txt" }, el("b", {}, activityTitle(i)), el("span", {}, LANES.find(l => l[0] === laneOf(i.title))[1])),
    el("time", {}, fmtTime(i.occurred_at))))) : el("p", { class: "note" }, "还没有记录。"), linkTo("activity", "全部活动"));
}
function capsCard(caps) {
  const on = caps.filter(c => !["UNAVAILABLE", "DISABLED"].includes(c.state));
  return sheet("能力状态", [el("div", { class: "caps" }, on.map(c => kv(CAP_NAMES[c.id] || c.id, tag(c.state)))),
    el("p", { class: "note mt" }, `另有 ${caps.length - on.length} 项没有开启或不可用。“可用”表示本地准入条件允许，不代表远端在线或已经执行。`)], linkTo("caps", "管理能力"));
}

/* ================= 活动时间线 ================= */
PAGES.activity = data => {
  const items = (data.items || []).filter(i => (state.actKind === "all" || laneOf(i.title) === state.actKind)
    && (!state.actFrom || localDay(i.occurred_at) >= state.actFrom) && (!state.actTo || localDay(i.occurred_at) <= state.actTo));
  const today = localDay(new Date().toISOString()), dayAgo = n => localDay(new Date(Date.now() - n * 864e5).toISOString());
  const presets = [["all", "全部日期", "", ""], ["today", "今天", today, today], ["yesterday", "昨天", dayAgo(1), dayAgo(1)], ["week", "近 7 天", dayAgo(6), today]];
  const preset = presets.find(([, , f, t]) => f === state.actFrom && t === state.actTo)?.[0] || "custom";
  const setRange = (f, t) => { state.actFrom = f; state.actTo = t; loadRoute({ reset: true }); };
  const dateBar = el("div", { class: "toolbar" }, el("div", { class: "seg", role: "group", "aria-label": "按日期筛选" },
      presets.map(([k, n, f, t]) => el("button", { type: "button", "aria-pressed": String(preset === k), onclick: () => setRange(f, t) }, n))),
    el("label", { class: "date-pick" }, el("span", {}, "从"), el("input", { class: "input", type: "date", id: "act-from", value: state.actFrom, max: today, onchange: e => setRange(e.target.value, state.actTo) })),
    el("label", { class: "date-pick" }, el("span", {}, "到"), el("input", { class: "input", type: "date", id: "act-to", value: state.actTo, max: today, onchange: e => setRange(state.actFrom, e.target.value) })));
  const days = new Map();
  for (const i of items) { const d = localDay(i.occurred_at) || "未记录"; if (!days.has(d)) days.set(d, []); days.get(d).push(i); }
  const todayKey = localDay(new Date().toISOString());
  const label = d => d === todayKey ? "今天" : d === localDay(new Date(Date.now() - 864e5).toISOString()) ? "昨天" : d.slice(5).replace("-", "月") + "日";
  return el("div", {}, head("活动时间线", "按时间倒序记录每一次接收、发送、保存和操作。点开一条可以看到它的编号。"), dateBar,
    el("div", { class: "toolbar" }, el("div", { class: "seg", role: "group", "aria-label": "按类型筛选" },
      [["all", "全部"], ...LANES.map(([k, n]) => [k, n])].map(([k, n]) => el("button", { type: "button", "aria-pressed": String(state.actKind === k), onclick: () => { state.actKind = k; rerender(); } }, n)))),
    items.length ? [...days].map(([d, list]) => el("section", { class: "tl-day" },
      el("div", { class: "tl-date" }, el("b", {}, label(d)), el("span", {}, d)),
      el("div", { class: "tl-list" }, list.map(i => {
        const b = el("button", { type: "button", class: "tl-item", "aria-expanded": "false", style: { "--k": `var(--lane-${laneOf(i.title)})` },
          onclick: () => b.setAttribute("aria-expanded", String(b.getAttribute("aria-expanded") !== "true")) },
          el("time", {}, fmtTime(i.occurred_at)), el("span", {}, el("b", {}, activityTitle(i)), el("span", { class: "meta" }, el("span", {}, LANES.find(l => l[0] === laneOf(i.title))[1]))),
          tag(i.status), el("span", { class: "tl-more" }, detailList(i.detail)));
        return b;
      })))) : empty("这里还没有记录", state.actFrom || state.actTo ? "这段日期里没有符合条件的记录。" : state.actKind === "all" ? "发生新的接收、发送或操作后，会出现在这里。" : "换一个类型看看。"),
    data.next_cursor ? el("div", { class: "more" }, btn("加载更早的记录", e => { e.currentTarget.disabled = true; loadRoute({ more: true }); })) : null);
};

/* ================= 记忆 ================= */
const MEM_KIND = { preference: "偏好", fact: "事实", plan: "计划", habit: "习惯", relation: "关系", profile: "资料", event: "事件", communication: "沟通", goal: "目标" };
const MEM_SUBJECT = { user: "关于你", owner: "关于你", self: "关于她", companion: "关于她" };
function memSearch(v) { state.memQ = v; clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadRoute({ reset: true }), 300); }
PAGES.memories = data => {
  const items = data.items || [];
  const searchInput = el("input", { class: "input", id: "mem-q", type: "search", maxlength: "100", placeholder: "搜索记忆内容", value: state.memQ,
    oninput: e => { if (e.isComposing) return; memSearch(e.target.value); }, oncompositionend: e => memSearch(e.target.value) });
  return el("div", {}, head("记忆", "每条记忆都有原话作为来源。确认后她会在以后的对话里用到；忘记会同时清除来源和由此衍生的内容。"),
    el("div", { class: "toolbar" }, el("div", { class: "seg", role: "group" },
      [["review", "待确认"], ["all", "全部"]].map(([k, n]) => el("button", { type: "button", "aria-pressed": String(state.memTab === k), onclick: () => { state.memTab = k; loadRoute({ reset: true }); } },
        n, k === "review" && state.nav?.review ? el("span", { class: "count" }, String(state.nav.review)) : null))),
      el("label", { class: "search" }, icon("search"), searchInput), el("span", { class: "spacer" }),
      el("div", { class: "seg", role: "group", "aria-label": "视图" }, [["list", "列表", "timeline"], ["graph", "关系图", "spark"]].map(([k, n, ic]) =>
        el("button", { type: "button", "aria-pressed": String(state.memView === k), onclick: () => { state.memView = k; rerender(); } }, icon(ic), n)))),
    state.memView === "graph" ? memoryGraph(items) : items.length ? el("div", { class: "mem-list" }, items.map(m => {
      const d = m.detail || {}, review = m.status !== "CONFIRMED" || d.needs_review;
      return el("article", { class: "mem" + (review ? " review" : "") },
        el("div", {}, el("h3", {}, m.title), el("div", { class: "meta" }, tag(m.status), d.memory_kind ? el("span", { class: "chip" }, MEM_KIND[d.memory_kind] || "其他") : null,
          d.subject ? el("span", {}, MEM_SUBJECT[d.subject] || "") : null, el("span", {}, "更新于 " + fmtDate(m.occurred_at)),
          Array.isArray(d.evidence_ids) ? el("span", {}, "来自 " + d.evidence_ids.length + " 条消息") : null),
          d.needs_review ? el("div", { class: "alert mt" }, icon("warn"), el("span", {}, "这条记忆和其他说法可能冲突，需要你核对。")) : null),
        el("div", { class: "acts" }, m.status !== "CONFIRMED" ? btn("确认", () => memoryAction(m, "confirm"), { cls: "sm primary", symbol: "check" }) : null,
          btn("纠正", () => memoryAction(m, "correct"), { cls: "sm", symbol: "edit" }), btn("忘记", () => memoryAction(m, "forget"), { cls: "sm danger", symbol: "trash" })));
    })) : empty(state.memQ ? "没有匹配的记忆" : state.memTab === "review" ? "没有待确认的记忆" : "还没有记忆",
      state.memQ ? "换个关键词试试。" : "新的记忆候选出现后，会先放在“待确认”里等你确认。"));
};
function memoryAction(item, decision) {
  const titles = { confirm: "确认这条记忆", correct: "纠正这条记忆", forget: "忘记这条记忆？" };
  const form = el("form", { class: "dlg-form" });
  let input;
  if (decision === "forget") form.append(el("p", {}, "“" + item.title + "”"), el("ul", { class: "consequence" },
    el("li", {}, "清除这条记忆和它引用的原始消息"), el("li", {}, "失效可能复述它的近期回复、摘要与自我观察"),
    el("li", {}, "从旧备份恢复时也会重新执行这次遗忘"), el("li", {}, "已经发到 QQ 的消息无法撤回")));
  else if (decision === "correct") {
    input = el("textarea", { class: "input", id: "memory-correction", required: true, rows: "4", maxlength: "500", value: item.title });
    form.append(el("p", { class: "muted" }, "会创建一条替代记录，原记录保留修订关系，不会被直接覆盖。"),
      el("label", { class: "field" }, el("span", {}, "原内容"), el("input", { class: "input", value: item.title, disabled: true })),
      el("label", { class: "field" }, el("span", {}, "正确的内容"), input));
  } else form.append(el("p", {}, "“" + item.title + "”"), el("p", { class: "muted" }, "确认后她会在以后的对话里用到这条记忆。"));
  const submit = el("button", { type: "submit", class: "btn " + (decision === "forget" ? "danger solid" : "primary") },
    decision === "forget" ? "忘记这条记忆" : decision === "correct" ? "保存纠正" : "确认");
  form.append(el("div", { class: "dlg-acts" }, submit));
  form.addEventListener("submit", e => {
    e.preventDefault();
    const body = { decision, ...(input ? { corrected_content: input.value.trim() } : {}) };
    if (input && !body.corrected_content) { formError(form, "请填写正确的内容。"); return; }
    perform(form, submit, () => api.write("/memories/" + encodeURIComponent(item.id) + "/decision", body, "POST",
      r => typeof r.memory_id === "string" && typeof r.state === "string" && (r.memory_id === String(item.id) || decision === "correct" && String(r.supersedes) === String(item.id))),
      async r => { closeDialog(); toast({ confirm: "已确认", correct: "已保存纠正", forget: "已忘记" }[decision] + "（" + statusOf(r.state)[0] + "）"); await loadRoute({ reset: true }); });
  });
  openDialog(titles[decision], form);
}

/* ================= 记忆关系图 =================
   参考 AstrBot 插件 LivingMemory 的图谱视图（选中节点列出关联记忆、图谱与列表互相定位）。
   现有记忆只有类型、主体和来源消息，所以这里按“主体 → 类型 → 记忆”连接，
   并把引用同一条来源消息的记忆连起来；实体与关系抽取需要后端新增后再接入。 */
const GPOS = new Map();
function graphModel(items) {
  const nodes = [{ id: "me", label: "你", type: "self" }], edges = [];
  const herItems = items.filter(m => ["self", "companion"].includes(m.detail?.subject));
  if (herItems.length) { nodes.push({ id: "her", label: "她", type: "her" }); edges.push({ a: "me", b: "her", kind: "root" }); }
  const kinds = new Map();
  for (const m of items) {
    const root = ["self", "companion"].includes(m.detail?.subject) ? "her" : "me", kind = m.detail?.memory_kind || "other";
    const cid = root + ":" + kind;
    if (!kinds.has(cid)) { kinds.set(cid, true); nodes.push({ id: cid, label: MEM_KIND[kind] || "其他", type: "kind", root }); edges.push({ a: root, b: cid, kind: "root" }); }
    nodes.push({ id: "m:" + m.id, label: m.title.length > 12 ? m.title.slice(0, 11) + "…" : m.title, type: "mem", m, cat: cid });
    edges.push({ a: cid, b: "m:" + m.id, kind: m.detail?.needs_review ? "conflict" : m.status === "CONFIRMED" ? "solid" : "hyp" });
  }
  const byEvidence = new Map();
  for (const m of items) for (const e of m.detail?.evidence_ids || []) { if (!byEvidence.has(e)) byEvidence.set(e, []); byEvidence.get(e).push(m.id); }
  const seen = new Set();
  for (const ids of byEvidence.values()) for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
    const key = ids[i] + "|" + ids[j]; if (seen.has(key)) continue; seen.add(key);
    edges.push({ a: "m:" + ids[i], b: "m:" + ids[j], kind: "shared" });
  }
  return { nodes, edges };
}
function layoutGraph(nodes, edges) {
  const W = 1000, H = 620, cx = W / 2, cy = H / 2, kindsList = nodes.filter(n => n.type === "kind");
  for (const n of nodes) {
    if (GPOS.has(n.id)) continue;
    if (n.type === "self") GPOS.set(n.id, { x: cx, y: cy });
    else if (n.type === "her") GPOS.set(n.id, { x: cx + 260, y: cy - 170 });
    else if (n.type === "kind") { const k = kindsList.indexOf(n), a = k / Math.max(1, kindsList.length) * Math.PI * 2 - Math.PI / 2; GPOS.set(n.id, { x: cx + Math.cos(a) * 210, y: cy + Math.sin(a) * 160 }); }
    else { const p = GPOS.get(n.cat) || { x: cx, y: cy }, siblings = nodes.filter(x => x.cat === n.cat), k = siblings.indexOf(n), a = Math.atan2(p.y - cy, p.x - cx) + (k - (siblings.length - 1) / 2) * .55;
      GPOS.set(n.id, { x: p.x + Math.cos(a) * 120, y: p.y + Math.sin(a) * 100 }); }
  }
  for (let it = 0; it < 160; it++) for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = GPOS.get(nodes[i].id), b = GPOS.get(nodes[j].id); const dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy) || 1;
    if (d < 92) { const push = (92 - d) / 2, ux = dx / d, uy = dy / d;
      if (nodes[i].type !== "self") { a.x += ux * push; a.y += uy * push; } if (nodes[j].type !== "self") { b.x -= ux * push; b.y -= uy * push; } }
  }
  for (const n of nodes) { const p = GPOS.get(n.id); p.x = Math.max(60, Math.min(W - 60, p.x)); p.y = Math.max(36, Math.min(H - 36, p.y)); }
}
function edgeD(e) { const a = GPOS.get(e.a), b = GPOS.get(e.b); const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1, bend = e.kind === "shared" ? 40 : 10;
  return `M${a.x.toFixed(1)} ${a.y.toFixed(1)} Q${(mx - dy / d * bend).toFixed(1)} ${(my + dx / d * bend).toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`; }
function memoryGraph(items) {
  if (!items.length) return empty("还没有可以画成关系图的记忆", state.memTab === "review" ? "切到“全部”看看。" : "记忆形成后会出现在这里。");
  const { nodes, edges } = graphModel(items); layoutGraph(nodes, edges);
  const sel = nodes.find(n => n.id === state.gSel) || null;
  const near = sel ? new Set([sel.id, ...edges.filter(e => e.a === sel.id || e.b === sel.id).flatMap(e => [e.a, e.b])]) : null;
  const q = state.memQ.trim();
  const svg = el("svg", { class: "graph", viewBox: "0 0 1000 620", role: "img", "aria-label": `记忆关系图，${nodes.length} 个节点` },
    edges.map(e => el("path", { class: "ge " + e.kind + (near && !(near.has(e.a) && near.has(e.b)) ? " dim" : ""), d: edgeD(e), "data-a": e.a, "data-b": e.b })),
    nodes.map(n => { const p = GPOS.get(n.id), r = n.type === "self" ? 30 : n.type === "her" ? 24 : n.type === "kind" ? 20 : 11;
      const dim = near ? !near.has(n.id) : q ? !(n.label.includes(q) || n.m?.title.includes(q)) && n.type === "mem" : false;
      return el("g", { class: `gn ${n.type}${n === sel ? " sel" : ""}${dim ? " dim" : ""}${n.m && n.m.status !== "CONFIRMED" ? " pending" : ""}`, transform: `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`,
        tabindex: "0", role: "button", "data-gn": n.id, "aria-label": n.m ? n.m.title : n.label },
        el("circle", { r: String(r) }), el("text", { y: String(n.type === "self" || n.type === "her" ? 5 : n.type === "kind" ? 5 : r + 15), class: n.type === "mem" ? "out" : "in" }, n.label));
    }));
  bindGraphEvents(svg, edges);
  return el("div", { class: "gwrap" }, el("section", { class: "gcanvas" }, svg,
    el("div", { class: "glegend" }, el("span", {}, el("i", { class: "l solid" }), "已确认"), el("span", {}, el("i", { class: "l hyp" }), "待确认"),
      el("span", {}, el("i", { class: "l conflict" }), "需要核对"), el("span", {}, el("i", { class: "l shared" }), "来自同一条消息"), el("span", { class: "muted" }, "拖动节点调整位置"),
      btn("重新排列", () => { GPOS.clear(); rerender(); }, { cls: "sm quiet", symbol: "refresh" }))),
    el("aside", { class: "gpanel", "aria-live": "polite" }, sel ? graphPanel(sel, nodes, edges) : graphSummary(items, nodes, edges)));
}
function graphSummary(items, nodes, edges) {
  const kinds = nodes.filter(n => n.type === "kind");
  return el("div", {}, el("h3", {}, "关系概览"), el("div", { class: "gstats" },
      el("div", {}, el("b", { class: "num" }, String(items.length)), el("span", {}, "条记忆")), el("div", {}, el("b", { class: "num" }, String(kinds.length)), el("span", {}, "个类型")),
      el("div", {}, el("b", { class: "num" }, String(items.filter(m => m.status !== "CONFIRMED").length)), el("span", {}, "条待确认"))),
    el("p", { class: "note mt" }, "点一个节点，右边会列出相关的记忆，可以直接确认、纠正或忘记。"),
    el("h4", { class: "gsub" }, "各类型"), el("ul", { class: "gtop" }, kinds.map(k => el("li", {}, el("button", { type: "button", onclick: () => pick(k.id) }, el("i"), k.label,
      el("span", { class: "num" }, String(nodes.filter(n => n.cat === k.id).length)))))));
}
function graphPanel(sel, nodes, edges) {
  const mems = sel.type === "mem" ? [sel.m, ...edges.filter(e => e.kind === "shared" && (e.a === sel.id || e.b === sel.id)).map(e => nodes.find(n => n.id === (e.a === sel.id ? e.b : e.a)).m)]
    : nodes.filter(n => n.type === "mem" && (n.cat === sel.id || (sel.type !== "kind" && nodes.find(k => k.id === n.cat)?.root === sel.id))).map(n => n.m);
  return el("div", {}, el("div", { class: "gp-head" }, el("button", { type: "button", class: "icon-btn", "aria-label": "返回概览", onclick: () => pick(null) }, icon("left")),
      el("h3", {}, sel.type === "mem" ? "这条记忆" : sel.label)),
    el("p", { class: "note" }, sel.type === "mem" && mems.length > 1 ? `还有 ${mems.length - 1} 条记忆来自同一条消息` : `${mems.length} 条记忆`),
    mems.map(m => el("article", { class: "gmem" + (m.status !== "CONFIRMED" ? " review" : "") }, el("b", {}, m.title),
      el("div", { class: "meta" }, tag(m.status), m.detail?.memory_kind ? el("span", { class: "chip" }, MEM_KIND[m.detail.memory_kind] || "其他") : null, el("span", { class: "note" }, fmtDate(m.occurred_at))),
      el("div", { class: "acts" }, m.status !== "CONFIRMED" ? btn("确认", () => memoryAction(m, "confirm"), { cls: "sm primary", symbol: "check" }) : null,
        btn("纠正", () => memoryAction(m, "correct"), { cls: "sm", symbol: "edit" }), btn("忘记", () => memoryAction(m, "forget"), { cls: "sm danger", symbol: "trash" })))));
}
function pick(id) { state.gSel = id; rerender(); if (id) $(`[data-gn="${CSS.escape(id)}"]`)?.focus({ preventScroll: true }); }
function bindGraphEvents(svg, edges) {
  let drag = null;
  const pt = ev => { const m = svg.getScreenCTM?.(); if (!m) return null; const p = svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY; return p.matrixTransform(m.inverse()); };
  svg.addEventListener("pointerdown", ev => { const g = ev.target.closest("[data-gn]"); if (!g) return; const p = pt(ev); const o = GPOS.get(g.dataset.gn);
    drag = { id: g.dataset.gn, g, sx: p ? p.x : 0, sy: p ? p.y : 0, ox: o.x, oy: o.y, moved: false }; g.setPointerCapture?.(ev.pointerId); });
  svg.addEventListener("pointermove", ev => { if (!drag) return; const p = pt(ev); if (!p) return; const dx = p.x - drag.sx, dy = p.y - drag.sy;
    if (Math.hypot(dx, dy) > 3) drag.moved = true; if (!drag.moved) return;
    const pos = { x: Math.max(30, Math.min(970, drag.ox + dx)), y: Math.max(30, Math.min(590, drag.oy + dy)) }; GPOS.set(drag.id, pos);
    drag.g.setAttribute("transform", `translate(${pos.x.toFixed(1)} ${pos.y.toFixed(1)})`);
    $$(".ge", svg).forEach(path => { if (path.dataset.a === drag.id || path.dataset.b === drag.id) path.setAttribute("d", edgeD({ a: path.dataset.a, b: path.dataset.b, kind: path.classList.contains("shared") ? "shared" : "" })); }); });
  const end = () => { if (!drag) return; const d = drag; drag = null; if (!d.moved) pick(state.gSel === d.id ? null : d.id); };
  svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", () => { drag = null; });
  svg.addEventListener("keydown", ev => { const g = ev.target.closest("[data-gn]"); if (g && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); pick(g.dataset.gn); } });
}

/* ================= 日记与成长 ================= */
const WEEK = ["日", "一", "二", "三", "四", "五", "六"];
PAGES.life = data => {
  const items = data.items || [];
  const diaries = items.filter(i => i.kind === "diary" && /^\d{4}-\d{2}-\d{2}$/.test(i.detail?.local_date || ""));
  const days = new Set(diaries.map(i => i.detail.local_date));
  if (!state.diaryDay || !/^\d{4}-\d{2}-\d{2}$/.test(state.diaryDay)) state.diaryDay = diaries[0]?.detail.local_date || localDay(new Date().toISOString());
  const [y, mo] = state.diaryDay.split("-").map(Number), first = new Date(Date.UTC(y, mo - 1, 1)).getUTCDay(), total = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  const today = localDay(new Date().toISOString());
  const cells = [...Array(first)].map(() => el("span"));
  for (let d = 1; d <= total; d++) {
    const key = `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`, has = days.has(key);
    cells.push(el("button", { type: "button", class: (has ? "has" : "") + (key === today ? " today" : ""), "aria-pressed": String(key === state.diaryDay),
      "aria-label": `${mo}月${d}日${has ? "，有日记" : ""}`, onclick: () => { state.diaryDay = key; rerender(); } }, String(d)));
  }
  const shift = n => { const dt = new Date(Date.UTC(y, mo - 1 + n, 1)); state.diaryDay = dt.toISOString().slice(0, 10); rerender(); };
  const goals = items.filter(i => i.kind === "goal"), reflections = items.filter(i => i.kind === "reflection");
  const paper = el("article", { class: "paper", id: "diary-paper", "aria-live": "polite" }, el("p", { class: "note" }, "正在读取日记…"));
  queueMicrotask(() => loadDiary(state.diaryDay, paper));
  return el("div", {}, head("日记与成长", "日记由独立进程在自然日结束后整理，只根据核验过的真实互动书写。", btn("暂停或恢复写日记", openModeDialog, { symbol: "pause" })),
    el("div", { class: "diary-wrap" },
      el("div", { class: "stack" },
        el("section", { class: "sheet cal" }, el("div", { class: "cal-head" },
          el("button", { type: "button", class: "icon-btn", "aria-label": "上个月", onclick: () => shift(-1) }, icon("left")), el("b", {}, `${y} 年 ${mo} 月`),
          el("button", { type: "button", class: "icon-btn", "aria-label": "下个月", onclick: () => shift(1) }, icon("right"))),
          el("div", { class: "cal-grid" }, WEEK.map(w => el("span", { class: "wd" }, w)), cells),
          el("div", { class: "cal-legend" }, el("span", {}, el("span", { class: "tag ok" }), "最近 14 篇日记"), el("span", {}, "其他日期也可以点开查看"))),
        sheet("目标与承诺", goals.length ? goals.map(g => el("div", { class: "goal" }, el("b", {}, g.title), tag(g.status),
          el("p", {}, g.detail?.commitment_state ? "承诺：" + statusOf(g.detail.commitment_state)[0] : "更新于 " + fmtDate(g.occurred_at)))) : el("p", { class: "note" }, "还没有目标。"))),
      el("div", { class: "stack" }, paper,
        sheet("最近的反思", reflections.length ? el("ul", { class: "rows" }, reflections.map(r => el("li", { class: "block" }, el("b", {}, r.title),
          el("span", { class: "meta-line" }, tag(r.status), el("span", { class: "note" }, fmtDate(r.occurred_at)))))) : el("p", { class: "note" }, "还没有反思记录。")))));
};
async function loadDiary(day, paper) {
  const epoch = state.epoch;
  try {
    const r = await api.get("/diary/" + day);
    if (epoch !== state.epoch || !paper.isConnected) return;
    if (r.local_date !== day || typeof r.body_md !== "string") throw new ApiError("CONSOLE_RESPONSE_INVALID");
    paper.replaceChildren(el("div", { class: "paper-head" }, el("div", {}, el("h2", {}, r.title || day + " 的日记"),
      el("p", {}, (Number.isInteger(r.version) ? "第 " + r.version + " 版，" : "") + r.body_md.length + " 字")),
      el("div", { class: "actions" }, btn("修订", () => diaryAction(day, "correction", r.body_md), { cls: "sm", symbol: "edit" }),
        btn("删除", () => diaryAction(day, "forget", ""), { cls: "sm danger", symbol: "trash" }))),
      el("div", { class: "paper-body" }, r.body_md.split(/\n\s*\n/).filter(Boolean).map(p => el("p", {}, p))),
      el("div", { class: "paper-foot" }, el("span", {}, "只有你能查看")));
  } catch (error) {
    if (epoch !== state.epoch || !paper.isConnected) return;
    paper.replaceChildren(el("div", { class: "empty" }, el("h3", {}, error.code === "DIARY_NOT_FOUND" ? day + " 没有日记" : "日记暂时读不到"),
      el("p", {}, error.code === "DIARY_NOT_FOUND" ? "这一天没有写日记，或者已经删除。" : errorText(error))));
  }
}
function diaryAction(day, operation, current) {
  const form = el("form", { class: "dlg-form" });
  const input = operation === "correction" ? el("textarea", { class: "input serif", id: "diary-correction", required: true, rows: "12", value: current }) : null;
  if (input) form.append(el("p", { class: "muted" }, "修订会保留之前的版本，并标记为你修订。"), el("label", { class: "field" }, el("span", {}, "正文"), input));
  else form.append(el("ul", { class: "consequence" }, el("li", {}, "清除这天的正文和所有修订版本"), el("li", {}, "从旧备份恢复时，这天的副本也会被清除"), el("li", {}, "不会删除这天的对话记录和记忆")));
  const submit = el("button", { type: "submit", class: "btn " + (input ? "primary" : "danger solid") }, input ? "保存修订" : "删除这天的日记");
  form.append(el("div", { class: "dlg-acts" }, submit));
  form.addEventListener("submit", e => {
    e.preventDefault();
    const body = input ? { text: input.value.trim() } : {};
    if (input && !body.text) { formError(form, "请填写日记正文。"); return; }
    perform(form, submit, () => api.write("/diary/" + day + "/" + operation, body, "POST",
      r => typeof r.state === "string" && Boolean(r.state) || typeof r.entry_id === "string" && Number.isInteger(r.version)),
      async () => { closeDialog(); toast(input ? "已保存修订" : "已删除这天的日记"); await loadRoute({ reset: true }); });
  });
  openDialog(input ? "修订 " + day + " 的日记" : "删除 " + day + " 的日记？", form, { wide: !!input });
}
function openModeDialog() {
  const targets = [["proactive", "主动私聊", "不主动找你，也会取消还没发出的主动消息"], ["life", "自主活动", "不再自己安排阅读和整理，已接受的任务继续处理"], ["diary", "写日记", "暂停之后不再整理新的日记"]];
  const form = el("form", { class: "dlg-form" }, el("p", { class: "muted" }, "只影响她主动做的事，你发来的消息始终会回复。改动立即生效，不需要重启。"));
  const rows = targets.map(([key, name, sub]) => {
    const sw = el("select", { class: "input sm-select", "aria-label": name }, el("option", { value: "" }, "保持不变"), el("option", { value: "true" }, "恢复"), el("option", { value: "false" }, "暂停"));
    form.append(el("div", { class: "mode-row" }, el("div", {}, el("b", {}, name), el("span", {}, sub)), sw));
    return [key, sw];
  });
  const submit = el("button", { type: "submit", class: "btn primary" }, "保存");
  form.append(el("div", { class: "dlg-acts" }, submit));
  form.addEventListener("submit", e => {
    e.preventDefault();
    const changes = rows.filter(([, sw]) => sw.value).map(([key, sw]) => ({ target: key, enabled: sw.value === "true" }));
    if (!changes.length) { formError(form, "没有选择要改的项目。"); return; }
    perform(form, submit, async () => { for (const c of changes) await api.write("/mode", c, "POST", r => typeof r.enabled === "boolean" || r.state === "COMPLETE"); },
      async () => { closeDialog(); toast("已保存，立即生效"); await loadRoute({ reset: true }); });
  });
  openDialog("管理主动行为", form);
}

/* ================= 阅读与文件 ================= */
const CONTENT_KIND = { link: ["链接", "#3b4070"], qq_image: ["图片", "#2f8a62"], link_image: ["文章图", "#6e9bd0"], file: ["文件", "#6c7190"], video: ["视频", "#fb7299"] };
PAGES.content = data => {
  const items = (data.items || []).filter(i => state.contentF === "all" || (state.contentF === "running" ? ["run"].includes(statusOf(i.status)[1]) : ["warn", "bad"].includes(statusOf(i.status)[1])));
  return el("div", {}, head("阅读与文件", "你分享的链接、图片和文件。外部内容只作为证据进入对话，不能改变权限或人格。"),
    el("div", { class: "toolbar" }, el("div", { class: "seg" }, [["all", "全部"], ["running", "进行中"], ["attention", "需要留意"]].map(([k, n]) =>
      el("button", { type: "button", "aria-pressed": String(state.contentF === k), onclick: () => { state.contentF = k; rerender(); } }, n)))),
    items.length ? el("section", { class: "sheet table-wrap" }, el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "类型"), el("th", {}, "内容"), el("th", {}, "状态"), el("th", { class: "r" }, "时间"))),
      el("tbody", {}, items.map(i => { const [k, c] = CONTENT_KIND[i.kind] || ["内容", "#6c7190"];
        return el("tr", { tabindex: "0", onclick: () => contentDrawer(i), onkeydown: e => { if (e.key === "Enter") contentDrawer(i); } },
          el("td", {}, el("span", { class: "src", style: { background: c } }, k)), el("td", { class: "title-cell" }, i.title, i.summary ? el("small", {}, reasonText(i.summary)) : null),
          el("td", {}, tag(i.status)), el("td", { class: "r muted" }, fmtDate(i.occurred_at))); }))))
      : empty("还没有内容记录", state.contentF === "all" ? "你在私聊里分享链接、图片或文件后，会出现在这里。" : "换个筛选条件看看。"));
};
function contentDrawer(i) {
  drawer(i.title, fmtDate(i.occurred_at), [el("div", { class: "mb" }, tag(i.status)), i.summary ? el("p", { class: "mb" }, "原因：" + reasonText(i.summary)) : null,
    kv("记录编号", el("span", { class: "num" }, String(i.id))), detailList(i.detail)]);
}

/* ================= 表情素材 ================= */
const hue = h => parseInt(String(h || "0").slice(0, 6), 16) % 360;
PAGES.memes = data => {
  const items = data.items || [];
  return el("div", {}, head("表情素材", "表情先核验来源，再由模型理解含义，之后才会进入索引供回复选用。这里显示每次理解任务的结果。"),
    items.length ? el("div", { class: "memes" }, items.map(m => el("button", { type: "button", class: "meme", onclick: () => drawer("表情素材理解", fmtDate(m.occurred_at),
      [el("div", { class: "mb" }, tag(m.status)), m.summary ? el("p", { class: "mb" }, "原因：" + reasonText(m.summary)) : null, kv("任务编号", el("span", { class: "num" }, String(m.id))), detailList(m.detail)]) },
      el("div", { class: "pic", style: { background: `hsl(${hue(m.detail?.asset_sha256)} 55% 90%)` } }, tag(m.status), el("span", { class: "hash num" }, String(m.detail?.asset_sha256 || "").slice(0, 8) || "—")),
      el("div", { class: "cap" }, el("b", {}, "素材 " + (String(m.detail?.asset_sha256 || "").slice(0, 6) || shortId(m.id))), el("span", {}, fmtDate(m.occurred_at))))))
      : empty("还没有表情理解记录", "启用表情语义理解后，理解过的素材会出现在这里。"));
};

/* ================= 语音与节点 ================= */
const mmss = s => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
PAGES.voice = data => {
  const meta = data.meta || {}, items = data.items || [], nodes = items.filter(i => i.kind === "voice_node"), jobs = items.filter(i => i.kind === "voice_job");
  const policy = meta.reply_policy || {}, out = state.voiceTab === "out";
  const mine = jobs.filter(j => j.detail?.operation !== "tts"), hers = jobs.filter(j => j.detail?.operation === "tts");
  const list = out ? hers : mine;
  const stat = (label, value, sub) => el("div", { class: "vstat" }, el("span", {}, label), el("b", {}, value), sub ? el("small", {}, sub) : null);
  return el("div", {}, head("语音与节点", "语音节点负责听写和合成。联系记录过期不等于设备离线，只代表最近没有收到节点的消息。"),
    el("div", { class: "vstats" }, stat("语音能力", meta.enabled ? "已开启" : "没有开启", meta.enabled ? null : "在能力与开关里开启"),
      stat("近期有联系的节点", count(meta.nodes_recent) + " / " + count(meta.nodes_total)), stat("等待和处理中", count(meta.pending_jobs)),
      stat("投递待核对", count(meta.uncertain_deliveries)),
      stat("她的语音回复", policy.enabled ? (policy.mode === "voice_input" ? "只在你发语音时" : "短回复可以用语音") : "没有开启", policy.enabled ? "最多 " + count(policy.max_chars) + " 字" : null)),
    nodes.length ? el("div", { class: "nodes" }, nodes.map(n => el("section", { class: "node" }, el("h3", {}, n.title, tag(n.status, n.detail?.state_label)),
      kv("最近联系", fmtDate(n.detail?.last_seen_at)), kv("能力", (n.detail?.capabilities || []).map(c => ({ asr: "转写", tts: "合成" })[c] || "其他").join("、") || "未记录"),
      kv("配置核验", statusOf(n.detail?.manifest_state)[0])))) : null,
    el("section", { class: "sheet" }, el("div", { class: "sheet-head" }, el("div", { class: "seg", role: "tablist" },
        el("button", { type: "button", "aria-pressed": String(!out), onclick: () => { state.voiceTab = "in"; rerender(); } }, "你发的", el("span", { class: "count" }, String(mine.length))),
        el("button", { type: "button", "aria-pressed": String(out), onclick: () => { state.voiceTab = "out"; rerender(); } }, "她发的", el("span", { class: "count" }, String(hers.length))))),
      list.length ? el("ul", { class: "vlist" }, list.map(j => {
        const secs = j.detail?.input_duration_seconds, seed = [...String(j.id)].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7);
        const wave = Array.from({ length: 40 }, (_, k) => 4 + Math.round(Math.abs(Math.sin(k * 1.37 + seed % 97) * Math.cos(k * .31 + seed % 13)) * 22));
        return el("li", { class: "vrow gone" + (out ? " out" : "") },
          el("span", { class: "vplay", title: "录音不在控制台保留", "aria-label": "录音不在控制台保留" }, icon("lock")),
          el("div", { class: "vmain" }, el("div", { class: "vtop" }, el("span", { class: "vwave", "aria-hidden": "true" }, wave.map(h => el("i", { style: { height: h + "px" } }))),
              el("span", { class: "vtime num" }, typeof secs === "number" ? mmss(secs) : "—")),
            el("p", { class: "vtext" }, j.summary || (out ? "她的语音回复" : "你发的语音")),
            el("div", { class: "vmeta" }, el("span", {}, fmtDate(j.occurred_at)), j.detail?.node_id ? el("span", {}, j.detail.node_id) : null, tag(j.status, j.detail?.state_label),
              j.detail?.delivery_state ? tag(j.detail.delivery_state) : null, j.detail?.partial ? el("span", {}, "音频不完整") : null, el("span", { class: "muted" }, "录音不保留"))));
      }))
        : el("p", { class: "sheet-body note" }, out ? "还没有她的语音回复。" : "还没有你发的语音。"),
      el("p", { class: "sheet-body note vnote" }, "录音和转写文字不会出现在控制台：任务结束后录音即清除，转写只进入对话。")));
};

/* ================= QQ 资料行为 ================= */
PAGES["account-actions"] = data => {
  const meta = data.meta || {}, items = data.items || [];
  const profile = f => items.find(i => i.kind === "account_profile" && i.detail?.field === f);
  const plans = items.filter(i => i.kind === "account_plan"), assets = items.filter(i => i.kind === "account_asset");
  const nick = profile("nickname"), sig = profile("signature"), av = profile("avatar");
  return el("div", {}, head("QQ 资料行为", "她对自己 QQ 资料的改动。计划、平台回执和实际观测分开记录，平台返回成功不等于资料已经变了。"),
    el("section", { class: "sheet qcompare" }, el("div", { class: "sheet-head" }, el("h2", {}, "平台上现在的资料"), el("span", { class: "note" }, nick?.detail?.observed_at ? "观测于 " + fmtDate(nick.detail.observed_at) : "还没有观测")),
      el("div", { class: "sheet-body qpair" }, el("div", { class: "qcard" }, el("span", { class: "qav ph", "aria-hidden": "true" }, av?.detail?.baseline_value ? "已记录" : "—"),
        el("div", { class: "qinfo" }, el("b", {}, nick?.detail?.baseline_value || "昵称尚未观测"), el("span", {}, sig?.detail?.baseline_value || "签名尚未观测"),
          el("small", {}, "观测可能是协议缓存，修改结果以平台回执为准"))))),
    Array.isArray(meta.limits) ? sheet("次数与冷却", el("div", { class: "limits" }, meta.limits.map(l => el("div", { class: "limit" }, el("b", {}, l.title), tag(l.state),
      el("span", { class: "note" }, "每天最多 " + count(l.daily_limit) + " 次，间隔 " + duration(l.cooldown_seconds)), l.reason ? el("span", { class: "note" }, l.reason) : null)))) : null,
    meta.policy_problem ? el("div", { class: "alert bad mt" }, icon("warn"), el("span", {}, meta.policy_problem)) : null,
    el("h2", { class: "section-title" }, "改动计划"),
    plans.length ? plans.map(p => el("section", { class: "sheet acct" }, el("div", { class: "acct-top" }, el("b", {}, p.title), el("span", { class: "row-gap" }, el("span", { class: "note" }, fmtDate(p.occurred_at)), tag(p.status, p.detail?.state_label))),
      el("div", { class: "compare" }, el("div", {}, el("h4", {}, "计划状态"), el("p", {}, statusOf(p.detail?.plan_state)[0])), el("div", {}, el("h4", {}, "平台回执"), el("p", {}, statusOf(p.detail?.receipt_state)[0]))),
      p.summary ? el("div", { class: "sheet-body acct-foot" }, el("span", { class: "note" }, p.summary)) : null)) : el("p", { class: "note" }, "还没有改动计划。"),
    assets.length ? [el("h2", { class: "section-title" }, "头像素材"), el("div", { class: "nodes" }, assets.map(a => el("section", { class: "node" }, el("h3", {}, a.title, tag(a.status)),
      detailList(a.detail, ["asset_sha256", "review_receipt_sha256"]))))] : null);
};

/* ================= 运行诊断 ================= */
const SCENARIOS = [["chat", "对话"], ["reply", "对话"], ["memory", "记忆整理"], ["diary", "日记"], ["reflection", "反思"], ["meme_caption", "表情理解"],
  ["social_autonomy", "QQ 资料行为"], ["content_vision", "图片理解"], ["content_video", "视频分析"], ["video", "视频分析"], ["media", "媒体会话"], ["tool", "工具任务"],
  ["wake", "自主活动"], ["autonomous", "自主活动"], ["exploration", "内容发现"], ["consolidation", "夜间整理"], ["goal", "目标"], ["affect", "情绪观察"], ["expression", "表达学习"]];
const scenarioName = s => (SCENARIOS.find(([k]) => String(s).startsWith(k)) || [, "其他场景"])[1];
const DELIVERY_ACTIONS = { "qq.private.reply": "私聊回复", "qq.private.proactive": "主动分享", "qq.private.poke": "拍一拍" };
PAGES.diagnostics = data => {
  const items = data.items || [], workers = items.filter(i => i.kind === "runtime"), usage = items.filter(i => i.kind === "budget");
  const problems = items.filter(i => !["runtime", "budget"].includes(i.kind));
  return el("div", {}, head("运行诊断", "进程记录、需要处理的问题和各场景的用量。这里不会显示私人内容、cookie 或密钥。"),
    sheet("工作进程", el("div", { class: "procs" }, workers.map(w => el("div", { class: "proc" }, el("b", {}, w.title), tag(w.status),
      el("small", {}, w.occurred_at ? "最近一次 " + fmtDate(w.occurred_at) : "还没有运行记录"))))),
    el("div", { class: "grid-2 mt-lg" },
      sheet("需要处理的问题", problems.length ? el("ul", { class: "rows" }, problems.map(p => el("li", { class: "prob" },
        el("span", {}, tag(p.status)), el("div", { class: "grow" }, el("b", {}, p.kind === "outbox" ? (DELIVERY_ACTIONS[p.detail?.action_type] || "消息投递") : p.kind === "jobs" ? "任务" : "内容读取"),
          el("p", { class: "note" }, p.summary ? reasonText(p.summary) : "没有原因代码"), el("p", { class: "num faint" }, String(p.id) + "，" + fmtDate(p.occurred_at)),
          p.kind === "outbox" && p.status === "DELIVERY_UNCERTAIN" ? reconcileBox(p) : null)))) : el("p", { class: "note" }, "没有需要处理的问题。")),
      sheet("用量明细", usage.length ? el("table", { class: "t" }, el("thead", {}, el("tr", {}, el("th", {}, "场景"), el("th", { class: "r" }, "次数"), el("th", {}, "状态"), el("th", { class: "r" }, "金额"))),
        el("tbody", {}, usage.map(u => el("tr", { class: "static" }, el("td", {}, scenarioName(u.title)), el("td", { class: "r num" }, count(u.detail?.count)), el("td", {}, tag(u.status)), el("td", { class: "r num" }, usd(u.detail?.billed_micro_usd))))))
        : el("p", { class: "note" }, "还没有模型用量记录。"))));
};
function reconcileBox(item) {
  const d = item.detail || {};
  if (!(d.can_reconcile === true && d.reconciliation_prepared === true && /^msg_[a-f0-9]{32}$/.test(String(item.id)) && DELIVERY_ACTIONS[d.action_type])) {
    return el("div", { class: "reconcile-note" }, el("b", {}, "需要先准备核对"), el("p", {}, typeof d.reconciliation_reason === "string" ? d.reconciliation_reason : "请先停止发送进程并准备核对凭证，完成后刷新此页。"));
  }
  const form = el("form", { class: "reconcile" }, el("p", { class: "note" }, "到 QQ 私聊里看看这条消息是否出现，再记录结果。这里不会重新发送。"));
  const choices = [["SENT", "QQ 里能看到，已送达", "primary"], ["CANCELLED", "QQ 里没有，结束核对", "danger"]];
  const buttons = choices.map(([outcome, label, cls]) => btn(label, async e => {
    if (state.deliverySaves.has(item.id)) return;
    state.deliverySaves.set(item.id, outcome); buttons.forEach(b => { b.disabled = true; });
    try {
      await perform(form, e.currentTarget, () => api.write("/deliveries/" + encodeURIComponent(item.id) + "/reconcile", { outcome }, "POST", r => r.state === outcome),
        async () => { toast(outcome === "SENT" ? "已记录为已送达" : "已结束核对，不会补发"); await loadRoute({ reset: true }); });
    } finally { state.deliverySaves.delete(item.id); if (form.isConnected) buttons.forEach(b => { b.disabled = false; }); }
  }, { cls: "sm " + cls }));
  form.addEventListener("submit", e => e.preventDefault());
  form.append(el("div", { class: "row-gap" }, buttons));
  return form;
}

/* ================= 配置字段（能力页与配置页共用一份草稿） ================= */
/* 服务端标签里的计价单位由输入框旁的单位显示，这里去掉，避免选人民币时仍写着美元 */
const cleanLabel = label => label.replace(/（微美元[^）]*）/g, "").replace(/（毫秒）/g, "");
const cfgFields = () => (state.data?.configuration?.fields || []).filter(f => typeof f.key === "string" && typeof f.label === "string").map(f => ({ ...f, label: cleanLabel(f.label) }));
const CAP_KEYS = new Set(Object.values(CAP_FIELDS).flat());
const capOfKey = key => Object.keys(CAP_FIELDS).find(id => CAP_FIELDS[id].includes(key));
/* 未配置的对象型设置开启时使用的默认值，与 reply_segments.SegmentedReplyConfig 的默认值一致 */
const NULL_DEFAULTS = { segmented_reply: { max_segments: 3, min_chars: 12, interval_ms: 1500, mode: "semantic" } };
/* 后端在默认值时会省略的子项，编辑时补出来显示（按句子模式时 mode 不写入） */
const SHOW_DEFAULTS = { segmented_reply: { mode: "sentence" } };
/* 常见表情分类。实际可用的分类取决于 Bridge 里导入的表情包，可以另外添加 */
const MEME_CATEGORY_PRESETS = ["happy", "like", "surprised", "morning", "sleep", "sad", "angry", "shy", "tired", "thinking", "love", "cry", "thanks", "sorry", "hug", "ok"];
function searchBox(id, value, placeholder, onValue) {
  // 输入法组字期间不重绘页面，否则中文无法输入。
  const input = el("input", { class: "input", id, type: "search", placeholder, value,
    oninput: e => { if (e.isComposing) return; onValue(e.target.value); }, oncompositionend: e => onValue(e.target.value) });
  return el("label", { class: "search" }, icon("search"), input);
}
const fieldOf = key => cfgFields().find(f => f.key === key);
const draftVal = f => state.draft.has(f.key) ? state.draft.get(f.key) : f.value;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
/* 金额字段：模型账本按微美元记账，选人民币时按配置汇率换算显示，保存时换算回微美元 */
function unitFor(key, type) {
  const usdScale = state.currency === "CNY" ? 1e12 / state.fx : 1e6, usdPre = state.currency === "CNY" ? "¥" : "$";
  if (/micro_usd$/.test(key)) return { scale: usdScale, pre: usdPre, money: true };
  if (/micro_cny$/.test(key)) return { scale: 1e6, pre: "¥", money: true };
  if (/_rate$/.test(key) && type === "integer") return { scale: usdScale, pre: usdPre, unit: "/ 百万 Token", money: true };
  if (/_ms$/.test(key)) return { scale: 1, unit: "毫秒" };
  if (/_seconds$/.test(key)) return { scale: 1, unit: "秒" };
  if (/_minutes$/.test(key)) return { scale: 1, unit: "分钟" };
  if (/_days$/.test(key)) return { scale: 1, unit: "天" };
  if (/_bytes$/.test(key)) return { scale: 1e6, unit: "MB" };
  if (/tokens$/.test(key)) return { scale: 1, unit: "Token" };
  if (/chars$/.test(key)) return { scale: 1, unit: "字" };
  return { scale: 1 };
}
const unitOf = f => unitFor(f.key, f.type);
const isHour = f => f.type === "integer" && /(_hour|^quiet_start|^quiet_end)$/.test(f.key);
const enumOf = f => Array.isArray(f.schema?.enum) ? f.schema.enum : null;
const minOf = f => f.minimum ?? f.schema?.minimum ?? f.schema?.exclusiveMinimum;
const maxOf = f => f.maximum ?? f.schema?.maximum ?? f.schema?.exclusiveMaximum;
function fmtNum(key, type, v) { const u = unitFor(key, type); return (u.pre || "") + (+(v / u.scale).toFixed(u.money ? 4 : 2)).toLocaleString("zh-CN") + (u.unit ? " " + u.unit : ""); }
function fmtField(f, v) {
  if (v === null || v === undefined) return "未配置";
  if (f.type === "secret") return "新密钥";
  if (f.type === "boolean") return v ? "开" : "关";
  if (isHour(f)) return String(v).padStart(2, "0") + ":00";
  if (["integer", "number"].includes(f.type)) return fmtNum(f.key, f.type, v);
  if (typeof v === "string") return VALUE_NAMES[v] || v;
  if (Array.isArray(v)) return v.join("、") || "（空）";
  if (typeof v === "object") return "已修改其中的设置";
  return String(v);
}
function setDraft(f, v) {
  if (f.type === "secret" ? v === "" : same(v, f.value)) state.draft.delete(f.key); else state.draft.set(f.key, v);
  rerender();
}
function setIn(obj, path, value) {
  const copy = JSON.parse(JSON.stringify(obj ?? {})); let cur = copy;
  for (const k of path.slice(0, -1)) { if (cur[k] === null || typeof cur[k] !== "object") cur[k] = {}; cur = cur[k]; }
  cur[path.at(-1)] = value; return copy;
}
function fieldError(node, text) { const p = $(".pf-err", node.closest(".pf, .sub-row")); if (p) { p.textContent = text; p.hidden = !text; } }
function numberInput({ id, key, type, value, nullable, lo, hi, onValue }) {
  const u = unitFor(key, type);
  const input = el("input", { class: "input", id, "data-k": id, inputmode: "decimal", value: value === null || value === undefined ? "" : String(+(value / u.scale).toFixed(u.money ? 6 : 4)),
    placeholder: nullable ? "未配置" : undefined, onchange: e => {
      const raw = e.target.value.trim();
      if (raw === "" && nullable) { onValue(null); return; }
      const n = Number(raw) * u.scale, v = type === "integer" ? Math.round(n) : n;
      if (raw === "" || !Number.isFinite(n) || n < 0 && lo === undefined || lo !== undefined && v < lo || hi !== undefined && v > hi) {
        fieldError(e.target, "请填写" + (lo !== undefined ? "不小于 " + fmtNum(key, type, lo) : "") + (hi !== undefined ? (lo !== undefined ? "、" : "") + "不大于 " + fmtNum(key, type, hi) : "") + "的数值。");
        return;
      }
      onValue(v);
    } });
  return el("span", { class: "unit" + (u.pre ? " has-pre" : "") }, u.pre ? el("i", {}, u.pre) : null, input, u.unit ? el("em", {}, u.unit) : null);
}
function control(f) {
  const v = draftVal(f), id = "cf-" + f.key.replace(/[^a-z0-9_]/gi, "-");
  if (f.key === "expression_meme_categories") return categoryPicker(f, v || []);
  if (f.type === "boolean") return el("button", { type: "button", class: "switch", role: "switch", id, "aria-checked": String(Boolean(v)), "data-k": id, "aria-label": f.label, onclick: () => setDraft(f, !draftVal(f)) });
  if (enumOf(f)) return el("select", { class: "input", id, "data-k": id, onchange: e => setDraft(f, JSON.parse(e.target.value)) },
    enumOf(f).map(o => el("option", { value: JSON.stringify(o), selected: same(o, v) }, VALUE_NAMES[o] || String(o))));
  if (isHour(f)) return el("select", { class: "input hour", id, "data-k": id, onchange: e => setDraft(f, Number(e.target.value)) },
    [...Array(24)].map((_, h) => el("option", { value: String(h), selected: h === v }, String(h).padStart(2, "0") + ":00")));
  if (["integer", "number"].includes(f.type)) return numberInput({ id, key: f.key, type: f.type, value: v, nullable: f.nullable, lo: minOf(f), hi: maxOf(f), onValue: x => setDraft(f, x) });
  if (f.type === "secret") return el("input", { class: "input", type: "password", id, "data-k": id, autocomplete: "new-password",
    placeholder: f.configured ? "已设置，留空保持不变" : "未设置", onchange: e => setDraft(f, e.target.value) });
  if (f.type === "array" && (Array.isArray(v) ? v.every(x => typeof x === "string") : true)) return listControl(id, v || [], list => setDraft(f, list));
  if (f.type === "object" && NULL_DEFAULTS[f.key]) {
    const on = v !== null && v !== undefined;
    const sw = el("div", { class: "kv toggle-row" }, el("span", {}, on ? "已开启" : "没有开启"),
      el("button", { type: "button", class: "switch", role: "switch", id, "data-k": id, "aria-checked": String(on), "aria-label": f.label,
        onclick: () => setDraft(f, on ? null : { ...NULL_DEFAULTS[f.key] }) }));
    return on ? el("div", { class: "obj" }, sw, objectEditor(f, { ...SHOW_DEFAULTS[f.key], ...v }, [], true)) : sw;
  }
  if (f.type === "object" && v === null) return el("span", { class: "note" }, "未配置，在部署配置里设置");
  if (["object", "array"].includes(f.type)) return state.jsonEdit?.has(f.key) || Array.isArray(v) ? jsonControl(f, id, v) : objectEditor(f, v, []);
  return el("input", { class: "input", id, "data-k": id, value: v ?? "", onchange: e => setDraft(f, e.target.value) });
}
function jsonControl(f, id, v) {
  return el("textarea", { class: "input code", id, rows: "6", spellcheck: "false", "data-k": id, value: JSON.stringify(v, null, 2), onchange: e => {
    let parsed; try { parsed = JSON.parse(e.target.value); } catch { fieldError(e.target, "格式不对，请检查括号和引号。"); return; }
    if (parsed === null && f.nullable) { setDraft(f, null); return; }
    if (!parsed || typeof parsed !== "object" || (f.type === "array") !== Array.isArray(parsed)) { fieldError(e.target, "填写的类型不对。"); return; }
    setDraft(f, parsed);
  } });
}
/* 对象配置按子项显示：开关、数字、文字、名单，嵌套的设置组缩进显示 */
function objectEditor(f, value, path, bare = false) {
  const parent = path.length ? path.at(-1) : f.key;
  const rows = Object.entries(value || {}).map(([k, v]) => {
    const p = [...path, k], id = "cf-" + [f.key, ...p].join("-").replace(/[^a-z0-9_-]/gi, "-"), label = subLabel(k);
    const put = x => setDraft(f, setIn(draftVal(f), p, x));
    let ctl;
    if (isSensitiveSub(k)) ctl = el("span", { class: "note" }, v ? "已设置，在部署配置里更换" : "未配置，在部署配置里设置");
    else if (k === "nodes" && v && typeof v === "object" && !Array.isArray(v)) ctl = el("span", { class: "note" }, Object.keys(v).length + " 个节点，在部署配置里设置");
    else if (typeof v === "boolean") ctl = el("button", { type: "button", class: "switch", role: "switch", id, "data-k": id, "aria-checked": String(v), "aria-label": label, onclick: () => put(!v) });
    else if (typeof v === "number") ctl = numberInput({ id, key: k, type: Number.isInteger(v) ? "integer" : "number", value: v, onValue: put });
    else if (typeof v === "string" && SUB_ENUMS[k]?.[parent]) ctl = el("select", { class: "input", id, "data-k": id, onchange: e => put(e.target.value) },
      SUB_ENUMS[k][parent].map(o => el("option", { value: o, selected: o === v }, VALUE_NAMES[o] || o)));
    else if (typeof v === "string") ctl = el("input", { class: "input", id, "data-k": id, value: v, onchange: e => put(e.target.value) });
    else if (Array.isArray(v) && v.every(x => typeof x === "string")) ctl = listControl(id, v, put);
    else if (v && typeof v === "object") return el("div", { class: "sub-group" }, el("p", { class: "sub-title" }, label), objectEditor(f, v, p));
    else ctl = el("span", { class: "note" }, v === null ? "未配置" : "在部署配置里设置");
    return el("div", { class: "sub-row" }, el("span", { class: "sub-l" }, label), el("span", { class: "sub-c" }, ctl), el("p", { class: "pf-err", role: "alert", hidden: true }));
  });
  if (path.length || bare) return el("div", { class: "sub-list" }, rows);
  return el("div", { class: "obj" }, el("div", { class: "sub-list" }, rows),
    btn("以 JSON 编辑", () => { state.jsonEdit = state.jsonEdit || new Set(); state.jsonEdit.add(f.key); rerender(); }, { cls: "sm quiet" }));
}
function categoryPicker(f, list) {
  const all = [...new Set([...MEME_CATEGORY_PRESETS, ...list])];
  const input = el("input", { class: "input", id: "cf-meme-cat", placeholder: "其他分类，例如 hungry", maxlength: "32" });
  const err = el("p", { class: "list-err", role: "alert", hidden: true });
  const add = () => { const x = input.value.trim().toLowerCase(); if (!x) return;
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(x)) { err.textContent = "分类名只能用小写英文字母、数字、短横线和下划线，并以字母开头。"; err.hidden = false; return; }
    if (!list.includes(x)) setDraft(f, [...list, x]); };
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  return el("div", { class: "cats" }, el("div", { class: "cat-grid" }, all.map(c => el("button", { type: "button", class: "cat" + (list.includes(c) ? " on" : ""), "aria-pressed": String(list.includes(c)),
      onclick: () => setDraft(f, list.includes(c) ? list.filter(x => x !== c) : [...list, c]) }, list.includes(c) ? icon("check") : null, VALUE_NAMES[c] || c))),
    el("div", { class: "list-add" }, input, btn("添加", add, { cls: "sm" })), err,
    el("p", { class: "note" }, `已选 ${list.length} 个，最多 12 个生效。实际能用的分类取决于 Bridge 里导入的表情包。`));
}
function listControl(id, list, onList) {
  const input = el("input", { class: "input", id, "aria-label": "添加一项", placeholder: "输入后按回车添加", maxlength: "64" });
  const err = el("p", { class: "list-err", role: "alert", hidden: true });
  const add = () => { const x = input.value.trim(); if (!x) return; if (list.includes(x)) { err.textContent = x + " 已经在里面了。"; err.hidden = false; return; } onList([...list, x]); };
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  return el("div", { class: "listed" }, el("ul", {}, list.length ? list.map((x, i) => el("li", {}, el("span", {}, VALUE_NAMES[x] || x),
    el("button", { type: "button", class: "icon-btn", "aria-label": "移除 " + x, onclick: () => onList(list.filter((_, j) => j !== i)) }, icon("close")))) : el("li", { class: "none" }, "还是空的")),
    el("div", { class: "list-add" }, input, btn("添加", add, { cls: "sm" })), err);
}
function fieldRow(f, label) {
  const changed = state.draft.has(f.key), wide = ["object", "array"].includes(f.type);
  const active = f.active_value !== undefined && !same(f.value, f.active_value) && f.type !== "secret";
  return el("div", { class: "pf" + (wide ? " wide" : "") + (changed ? " changed" : ""), id: "pf-" + f.key },
    el("div", { class: "pf-l" }, el("b", {}, label || f.label), f.description ? el("span", {}, f.description) : null,
      active ? el("span", { class: "pf-active" }, "当前生效：" + fmtField(f, f.active_value)) : null),
    el("div", { class: "pf-c" }, control(f), f.nullable && f.type !== "secret" && !NULL_DEFAULTS[f.key] && draftVal(f) !== null ? btn("设为未配置", () => setDraft(f, null), { cls: "sm quiet" }) : null),
    el("p", { class: "pf-err", role: "alert", hidden: true }));
}
function currencySwitch() {
  return el("div", { class: "seg", role: "group", "aria-label": "金额显示" }, [["CNY", "人民币"], ["USD", "美元"]].map(([k, n]) =>
    el("button", { type: "button", "aria-pressed": String(state.currency === k), onclick: () => { state.currency = k; rerender(); } }, n)));
}
function saveBar() {
  if (!state.draft.size || !DRAFT_ROUTES.includes(state.route) || !state.data?.configuration) return null;
  const c = state.data.configuration, next = c.version + 1;
  return el("div", { class: "savebar" }, el("p", {}, el("b", { class: "num" }, String(state.draft.size)), ` 项修改没保存。保存后成为 v${next}，重启后生效。`),
    btn("放弃修改", () => { state.draft.clear(); rerender(); }, { cls: "quiet" }), btn("查看修改", reviewDrawer, { cls: "quiet" }),
    btn("保存为 v" + next, saveDraft, { cls: "primary" }));
}
function diffLines(f, v) {
  if (f.type === "object" && f.value && v && typeof v === "object" && !Array.isArray(v)) {
    const out = [], walk = (a, b, path) => { for (const k of new Set([...Object.keys(a || {}), ...Object.keys(b || {})])) {
      const x = a?.[k], y = b?.[k];
      if (x && y && typeof x === "object" && typeof y === "object" && !Array.isArray(x)) walk(x, y, [...path, k]);
      else if (!same(x, y)) out.push([[f.label, ...path.map(subLabel), subLabel(k)].join(" › "), typeof x === "number" ? fmtNum(k, "number", x) : limitValue(k, x), typeof y === "number" ? fmtNum(k, "number", y) : limitValue(k, y)]); } };
    walk(f.value, v, []); return out;
  }
  return [[f.label, f.type === "secret" ? "原密钥" : fmtField(f, f.value), fmtField(f, v)]];
}
function diffList(undo) {
  return el("ul", { class: "diff" }, [...state.draft].flatMap(([k, v]) => { const f = fieldOf(k); if (!f) return [];
    return diffLines(f, v).map(([name, a, b], i) => el("li", {}, el("b", {}, name), el("span", {}, el("s", {}, a), " 改为 ", el("ins", {}, b)),
      undo && i === 0 ? btn("撤销", () => { state.draft.delete(k); rerender(); state.draft.size ? reviewDrawer() : closeDrawer(); }, { cls: "sm quiet" }) : null)); }));
}
function reviewDrawer() { drawer("待保存的修改", state.draft.size + " 项，可以逐项撤销", diffList(true)); }
function saveDraft() {
  const c = state.data.configuration, changes = Object.create(null);
  for (const [k, v] of state.draft) { const f = fieldOf(k); if (f && !(f.type === "secret" && v === "")) changes[k] = v; }
  if (!Object.keys(changes).length) { toast("没有需要保存的修改"); return; }
  const form = el("form", { class: "dlg-form" }, el("p", { class: "muted" }, `保存后成为待生效版本。运行中的请求继续使用 v${c.active_version}，按停止、备份、重启的顺序操作后才会生效。`), diffList(false));
  const submit = el("button", { type: "submit", class: "btn primary" }, "保存 " + Object.keys(changes).length + " 项修改");
  form.append(el("div", { class: "dlg-acts" }, submit));
  form.addEventListener("submit", e => {
    e.preventDefault();
    if (state.configSave) return;
    state.configSave = true;
    perform(form, submit, () => api.write("/configuration", { expected_version: c.version, changes }, "PUT",
      r => Number.isInteger(r.version) && Array.isArray(r.fields) && Number.isInteger(r.active_version)), async r => {
      const budget = state.data.configuration.cloud_budget;
      state.data.configuration = { ...r, cloud_budget: r.cloud_budget || budget };
      for (const k of Object.keys(changes)) state.draft.delete(k);
      closeDialog(); closeDrawer(); rerender(); toast(`已保存为 v${r.version}，重启后生效`);
    }).finally(() => { state.configSave = null; });
  });
  openDialog("保存为 v" + (c.version + 1), form);
}

/* ================= 能力与开关 ================= */
const capList = () => state.data?.capabilities?.capabilities || [];
const capFieldsOf = id => (CAP_FIELDS[id] || []).map(fieldOf).filter(Boolean);
const capMain = id => capFieldsOf(id).find(f => f.type === "boolean" && /_enabled$/.test(f.key));
/* 这些能力的开关或服务地址由部署配置保管，浏览器不能修改 */
const CAP_DEPLOY = {
  "media.voice": "网页语音会话的服务商地址、费用上限和会话时长在服务器部署配置的 [media] 段设置。",
  executive: "执行任务的端点和凭据在服务器部署配置的 [executive] 段设置，控制台不能修改。",
  "reading.web": "要读取的网站名单现在只能在服务器的部署环境里填写：读取适配器配置 COMPANION_OBSERVATION_ADAPTERS 里网页适配器的 allowed_hosts。控制台里填写需要后端先把名单从适配器凭据中拆出来。",
  "reading.bilibili": "B 站读取适配器和账号授权在部署环境里配置。", "reading.heybox": "小黑盒读取适配器在部署环境里配置。",
  "model.understand_video": "视频理解模型的审批窗口（paid_model）在部署配置里设置。",
  "qq.images": "付费图片理解的审批窗口（paid_model.image_input）在部署配置里设置。",
};
PAGES.caps = data => {
  const caps = capList(), known = new Set(CAP_GROUPS.flatMap(g => g[1]));
  const groups = [...CAP_GROUPS, ["其他", caps.map(c => c.id).filter(id => !known.has(id))]];
  const q = state.capQ.trim();
  const pick = c => (state.capF === "all" || (state.capF === "on" ? !["DISABLED", "UNAVAILABLE"].includes(c.state) : state.capF === "off" ? ["DISABLED", "UNAVAILABLE"].includes(c.state) : ["PAUSED", "ATTENTION", "BLOCKED"].includes(c.state)))
    && (!q || (CAP_NAMES[c.id] || "").includes(q) || (c.summary || "").includes(q) || capFieldsOf(c.id).some(f => f.label.includes(q)));
  const sel = caps.find(c => c.id === state.sub) || caps.find(pick) || caps[0];
  const filterCount = f => { const keep = state.capF; state.capF = f; const n = caps.filter(pick).length; state.capF = keep; return n; };
  const index = groups.map(([name, ids]) => {
    const list = ids.map(id => caps.find(c => c.id === id)).filter(Boolean), shown = list.filter(pick);
    if (!shown.length) return null;
    const open = q || !state.capCollapsed.has(name);
    return el("div", { class: "ci-group" }, el("button", { type: "button", class: "ci-ghead", "aria-expanded": String(Boolean(open)), onclick: () => { state.capCollapsed.has(name) ? state.capCollapsed.delete(name) : state.capCollapsed.add(name); rerender(); } },
      icon("right", "i chev"), el("span", {}, name), el("small", {}, list.filter(c => !["DISABLED", "UNAVAILABLE"].includes(c.state)).length + "/" + list.length)),
      open ? shown.map(c => { const main = capMain(c.id);
        return el("div", { class: "ci-row" + (c === sel ? " sel" : "") + (["DISABLED", "UNAVAILABLE"].includes(c.state) ? " off" : "") },
          el("a", { class: "ci-name", href: "#caps/" + encodeURIComponent(c.id), "aria-current": c === sel ? "true" : null, onclick: () => { state.capMobile = true; } },
            el("span", { class: "dot " + statusOf(c.state)[1], title: statusOf(c.state)[0] }), el("span", { class: "nm" }, CAP_NAMES[c.id] || "未命名能力"),
            capFieldsOf(c.id).some(f => state.draft.has(f.key)) ? el("span", { class: "pdot", title: "有没保存的修改" }) : null),
          main ? el("button", { type: "button", class: "switch sm", role: "switch", "aria-checked": String(Boolean(draftVal(main))), "aria-label": (CAP_NAMES[c.id] || "") + "总开关", onclick: () => setDraft(main, !draftVal(main)) }) : null);
      }) : null);
  });
  return el("div", {}, head("能力与开关", "左边找能力，右边看它的状态和参数。总开关和参数保存为新版本后重启生效；暂停立即生效。", currencySwitch()),
    el("div", { class: "caps2" + (state.capMobile ? " show-detail" : "") },
      el("aside", { class: "cap-index", "aria-label": "能力列表" },
        searchBox("cap-q", state.capQ, "搜索能力或参数", v => { state.capQ = v; rerender(); }),
        el("div", { class: "ci-filter", role: "group", "aria-label": "筛选" }, [["all", "全部"], ["on", "已开启"], ["off", "未开启"], ["warn", "需留意"]].map(([k, n]) =>
          el("button", { type: "button", "aria-pressed": String(state.capF === k), onclick: () => { state.capF = k; rerender(); } }, n + " ", el("span", { class: "num" }, String(filterCount(k)))))),
        el("div", { class: "ci-list" }, index.some(Boolean) ? index : el("div", { class: "empty" }, el("h3", {}, "没有符合条件的能力"), el("p", {}, "换个关键词，或选择“全部”。")))),
      el("section", { class: "cap-detail" }, sel ? capDetail(sel) : el("p", { class: "note" }, "还没有能力状态。"))));
};
PAGES.caps.after = () => { const s = $(".ci-row.sel"); if (s && !state.capMobile) s.scrollIntoView?.({ block: "nearest" }); };
function capDetail(c) {
  const name = CAP_NAMES[c.id] || "未命名能力", fields = capFieldsOf(c.id), main = capMain(c.id), rt = CAP_RUNTIME[c.id];
  const paused = (c.reason_codes || []).some(r => /PAUSED/.test(r));
  const limits = Object.entries(c.limits || {}).filter(([, v]) => v !== null && v !== undefined && v !== "");
  const reasons = [...new Set((c.reason_codes || []).map(reasonText))];
  return el("div", {}, btn("全部能力", () => { state.capMobile = false; rerender(); }, { cls: "sm quiet cd-back", symbol: "left" }),
    el("header", { class: "cd-head" }, el("div", {}, el("h2", {}, name), el("p", {}, c.summary || "")),
      el("div", { class: "cd-switch" }, tag(c.state), main ? el("label", {}, el("span", {}, "总开关"), control(main)) : null)),
    reasons.length ? el("ul", { class: "needs" }, reasons.map(r => el("li", { class: "no" }, icon("close"), el("span", {}, r)))) : null,
    CAP_DEPLOY[c.id] ? el("p", { class: "lockline" }, icon("lock"), el("span", {}, CAP_DEPLOY[c.id])) : null,
    el("div", { class: "cd-stats" },
      el("div", {}, el("span", {}, "额度与限制"), limits.length ? limits.map(([k, v]) => el("p", { class: "lim-line" }, el("small", {}, limitName(k)), el("b", { class: "num" }, limitValue(k, v))))
        : el("small", {}, "这项能力没有单独的额度")),
      el("div", {}, el("span", {}, "运行状态"), el("b", {}, statusOf(c.state)[0]), rt ? btn(paused ? "恢复" : "暂停", () => runtimeToggle(rt, paused), { cls: "sm", symbol: paused ? "refresh" : "pause" }) : null,
        el("small", {}, rt ? "立即生效，不需要重启" : "没有单独的暂停")),
      el("div", {}, el("span", {}, "由谁使用"), el("b", {}, (c.invocation || []).map(x => ({ automatic: "她自动使用", owner_command: "你的指令", tool: "工具调用", owner: "你发起" })[x] || "其他方式").join("、") || "未记录"),
        el("small", {}, c.runtime_observation && c.runtime_observation !== "UNOBSERVED" ? "运行观测：" + statusOf(c.runtime_observation)[0] : "还没有运行观测"))),
    ["proactive", "diary", "consolidation"].includes(c.id) ? el("section", { class: "cd-sec" }, el("h3", {}, "作息时间"), scheduleStrip()) : null,
    el("section", { class: "cd-sec" }, el("h3", {}, "参数", el("span", {}, fields.length ? "保存为新版本后生效" : "")),
      fields.length ? el("div", { class: "pf-list" }, fields.map(f => fieldRow(f))) : el("p", { class: "note" }, CAP_DEPLOY[c.id] ? "这项能力的设置都在部署配置里。" : "这项能力没有可以在控制台修改的参数。")));
}
function runtimeToggle(target, paused) {
  const name = { diary: "写日记", life: "自主活动", proactive: "主动私聊" }[target];
  const form = el("form", { class: "dlg-form" }, el("p", {}, paused ? `恢复${name}。` : `暂停${name}。`), el("p", { class: "muted" }, "立即生效，不需要重启。你发来的消息始终会回复。"));
  const submit = el("button", { type: "submit", class: "btn primary" }, paused ? "恢复" : "暂停");
  form.append(el("div", { class: "dlg-acts" }, submit));
  form.addEventListener("submit", e => { e.preventDefault();
    perform(form, submit, () => api.write("/mode", { target, enabled: paused }, "POST", r => typeof r.enabled === "boolean" || r.state === "COMPLETE"),
      async () => { closeDialog(); toast(paused ? "已恢复，立即生效" : "已暂停，立即生效"); await loadRoute({ reset: true }); }); });
  openDialog((paused ? "恢复" : "暂停") + name, form);
}

/* ================= 配置 ================= */
const GROUPS = [["model", "模型与计价"], ["memory", "记忆与日记"], ["autonomy", "主动与自主"], ["content", "阅读与文件"], ["expression", "表达与表情"], ["system", "运行与控制台"]];
const RATE_KEYS = ["input_rate", "cached_input_rate", "output_rate"];
const MODEL_TAIL = ["timeout_seconds", "max_output_tokens", "context_soft_tokens", "max_recent_messages"];
const SCHEDULE_KEYS = ["quiet_start", "quiet_end", "diary_generate_hour", "consolidation_local_hour"];
const isMoneyKey = k => /_(daily|monthly)_micro_usd$|^monthly_limit_micro_usd$/.test(k);
PAGES.configuration = data => {
  const c = data.configuration;
  if (!c || !Array.isArray(c.fields) || !Number.isInteger(c.version) || !Number.isInteger(c.active_version)) return empty("配置数据不完整", "请刷新核对，现在无法安全编辑配置。", true);
  const q = state.cfgQ.trim(), fields = cfgFields();
  const search = searchBox("cfg-q", state.cfgQ, "搜索设置，例如 冷却、上限、时间", v => { state.cfgQ = v; rerender(); });
  search.classList.add("wide-search");
  const top = [head("配置", "修改会先攒在一起，核对后保存为加密的新版本；运行中的请求继续使用旧配置，重启后生效。", currencySwitch()), el("div", { class: "toolbar" }, search)];
  if (q) {
    const hits = fields.filter(f => (f.label + (f.description || "") + f.key).includes(q));
    return el("div", {}, top, sheet(`“${q}”的搜索结果`, hits.length ? el("div", { class: "pf-list" }, hits.map(f => CAP_KEYS.has(f.key)
      ? el("div", { class: "pf" }, el("div", { class: "pf-l" }, el("b", {}, f.label), el("span", {}, "属于“" + (CAP_NAMES[capOfKey(f.key)] || "能力") + "”")),
          el("div", { class: "pf-c" }, el("a", { class: "btn sm", href: "#caps/" + encodeURIComponent(capOfKey(f.key)) }, "去能力与开关修改")))
      : fieldRow(f))) : el("div", { class: "empty" }, el("h3", {}, "没有找到这个设置"), el("p", {}, "试试“冷却”“上限”“时间”这类词。")), el("span", { class: "note" }, hits.length + " 项")));
  }
  const special = new Set([...RATE_KEYS, ...MODEL_TAIL, ...fields.filter(f => isMoneyKey(f.key) || f.type === "secret").map(f => f.key)]);
  // 属于某项能力的开关和参数只在“能力与开关”里出现，配置页只放系统级设置。
  const rest = fields.filter(f => !special.has(f.key) && !CAP_KEYS.has(f.key));
  const groups = GROUPS.filter(([g]) => rest.some(f => (f.group || "system") === g));
  const extra = [...new Set(rest.map(f => f.group || "system"))].filter(g => !GROUPS.some(([k]) => k === g)).map(g => [g, "其他设置"]);
  const sections = [["version", "版本"], ["model", "模型与密钥"], ["money", "费用与预算"],
    ...[...groups, ...extra].filter(([g]) => g !== "model")];
  const dirtyIn = id => ({ model: [...RATE_KEYS, ...MODEL_TAIL, ...fields.filter(f => f.type === "secret").map(f => f.key), ...rest.filter(f => f.group === "model").map(f => f.key)],
    money: fields.filter(f => isMoneyKey(f.key) && !CAP_KEYS.has(f.key)).map(f => f.key) }[id] || rest.filter(f => (f.group || "system") === id).map(f => f.key)).some(k => state.draft.has(k));
  return el("div", {}, top, el("div", { class: "cfg2" },
    el("nav", { class: "cfg-toc", "aria-label": "配置目录" }, sections.map(([id, n]) => el("a", { href: "#configuration/" + id, "data-toc": "cfg-" + id,
      onclick: e => { e.preventDefault(); history.replaceState(null, "", "#configuration/" + id); document.getElementById("cfg-" + id)?.scrollIntoView({ block: "start" }); } }, n,
      dirtyIn(id) || (id === "money" && state.budgetDraft.size) ? el("span", { class: "dirty", "aria-label": "有修改" }) : null))),
    el("div", { class: "cfg-main" }, versionSection(c), modelSection(rest.filter(f => f.group === "model")), moneySection(c),
      [...groups, ...extra].filter(([g]) => g !== "model").map(([g, n]) => el("section", { class: "sheet cfg-sec", id: "cfg-" + g }, el("div", { class: "sheet-head" }, el("h2", {}, n)),
        el("div", { class: "sheet-body pf-list" }, rest.filter(f => (f.group || "system") === g).map(f => fieldRow(f))))))));
};
PAGES.configuration.after = () => {
  if (state.sub && !state.cfgScrolled) { state.cfgScrolled = true; document.getElementById("cfg-" + state.sub)?.scrollIntoView({ block: "start" }); }
  spyToc();
};
function versionSection(c) {
  const hist = [...(c.history || [])].filter(h => Number.isInteger(h.version)).sort((a, b) => a.version - b.version).slice(-6);
  const label = v => v === c.active_version ? ["active", "生效中"] : v > c.active_version ? ["pending", "待生效"] : ["old", "历史"];
  return el("section", { class: "sheet cfg-sec", id: "cfg-version" }, el("div", { class: "sheet-head" }, el("h2", {}, "版本")), el("div", { class: "sheet-body" },
    el("ol", { class: "vchain" }, hist.map(h => { const [cls, text] = label(h.version);
      return el("li", { class: cls }, el("button", { type: "button", onclick: () => drawer("v" + h.version + " 的修改", fmtDate(h.created_at),
        el("ul", { class: "diff" }, (h.fields || []).map(k => el("li", {}, el("b", {}, fieldOf(k)?.label || "已移除的设置"))))) },
        el("b", { class: "num" }, "v" + h.version), el("span", {}, text), el("small", {}, fmtDate(h.created_at) + "，" + (h.fields || []).length + " 项修改"))); }),
      !hist.length ? el("li", { class: "active" }, el("span", {}, el("b", { class: "num" }, "v" + c.active_version), el("span", {}, "生效中"), el("small", {}, "还没有通过控制台保存过修改"))) : null,
      !c.pending_restart ? el("li", { class: "next" }, el("span", {}, el("b", { class: "num" }, "v" + (c.version + 1)), el("span", {}, state.draft.size ? state.draft.size + " 项修改未保存" : "暂无修改"))) : null),
    c.pending_restart ? el("div", { class: "apply" }, el("div", {}, el("h3", {}, `v${c.version} 已保存，还没有生效`), el("p", { class: "note" }, "按下面的顺序在服务器上操作，完成重启后这里会显示为生效中。"),
      el("ol", { class: "steps" }, el("li", {}, "停止聊天、日记、内容和生活进程"), el("li", {}, "做一次加密备份"), el("li", {}, "如果在 QQ 私人窗口期内，先完成窗口交接"), el("li", {}, "重启 API 和各个进程")))) : null));
}
function modelSection(other) {
  const rate = k => fieldOf(k), rates = RATE_KEYS.map(rate).filter(Boolean);
  const ctx = fieldOf("context_soft_tokens"), out = fieldOf("max_output_tokens"), inR = rate("input_rate"), outR = rate("output_rate");
  const est = ctx && out && inR && outR ? (draftVal(ctx) * draftVal(inR) + draftVal(out) * draftVal(outR)) / 1e6 : null;
  // 请求地址和密钥放在一起；视频模型的请求地址属于部署保管的 paid_model，这里只能换密钥。
  const order = ["provider", "model_url", "model_name"], main = [...order.map(fieldOf), ...other.filter(f => !order.includes(f.key))].filter(Boolean);
  const key = fieldOf("model_key"), videoKey = fieldOf("video_model_key");
  return el("section", { class: "sheet cfg-sec", id: "cfg-model" }, el("div", { class: "sheet-head" }, el("h2", {}, "模型与密钥"), el("span", { class: "note" }, "主回复只使用这一个模型")),
    el("div", { class: "sheet-body" }, el("h3", { class: "sub first" }, "对话模型"),
      el("div", { class: "pf-list" }, main.map(f => fieldRow(f, f.key === "model_url" ? "请求地址" : f.key === "model_name" ? "模型名称" : f.key === "provider" ? "服务类型" : undefined)),
        key ? fieldRow(key, "密钥") : null),
      videoKey ? [el("h3", { class: "sub" }, "视频理解模型"), el("div", { class: "pf-list" }, fieldRow(videoKey, "密钥"),
        el("div", { class: "pf" }, el("div", { class: "pf-l" }, el("b", {}, "请求地址"), el("span", {}, "在服务器部署配置的 paid_model.video_input 里设置，控制台不能修改。"))))] : null,
      el("p", { class: "note mt" }, "密钥只能写入，任何地方都不会显示原值。传输令牌、主人控制令牌和读取适配器凭据在部署环境里更换；主人控制令牌同时用来加密配置版本，直接更换会导致旧版本无法解密。"),
      rates.length ? [el("h3", { class: "sub" }, "费率", el("span", {}, "按服务商公布的峰值价格填写，价格未知时会停止自主活动。")),
        el("div", { class: "prices" }, rates.map(f => el("div", { class: "price" + (state.draft.has(f.key) ? " changed" : "") }, el("span", {}, ({ input_rate: "输入", cached_input_rate: "缓存命中输入", output_rate: "输出" })[f.key]), control(f))))] : null,
      el("div", { class: "pf-list" }, MODEL_TAIL.map(fieldOf).filter(Boolean).map(f => fieldRow(f))),
      est !== null ? el("p", { class: "estimate" }, icon("coin"), el("span", {}, "按现在的设置，一轮上下文用满的对话最多约 ", el("b", { class: "num" }, money(est)),
        state.currency === "CNY" ? "（按 1 美元 = ¥" + (state.fx / 1e6).toFixed(2) + " 换算）" : "")) : null));
}
function scheduleStrip() {
  const fs = SCHEDULE_KEYS.map(fieldOf).filter(Boolean); if (!fs.length) return null;
  const v = k => { const f = fieldOf(k); return f ? draftVal(f) : null; };
  const qs = v("quiet_start"), qe = v("quiet_end"), x = h => (h / 24 * 100) + "%";
  const bands = Number.isInteger(qs) && Number.isInteger(qe) ? (qs > qe ? [[qs, 24], [0, qe]] : qs < qe ? [[qs, qe]] : []) : [];
  // 标记按时间排开，相隔不到 4 小时的错开到下一行，文字不压线也不互相重叠。
  const rowsUsed = [];
  const pin = (k, name, cls, on) => {
    if (!Number.isInteger(v(k))) return null;
    const h = v(k); let row = 0; while (rowsUsed.some(([r, at]) => r === row && Math.abs(at - h) < 4)) row++; rowsUsed.push([row, h]);
    const right = h >= 19;
    return el("div", { class: "pin " + cls + (on === false ? " off" : "") + (right ? " right" : ""), style: { left: x(h + .5), top: (10 + row * 26) + "px" } },
      el("i"), el("span", {}, name + " " + String(h).padStart(2, "0") + ":00"));
  };
  const now = localMinutes(new Date().toISOString());
  return el("div", { class: "cd-clock" }, el("div", { class: "clock", role: "img", "aria-label": "免打扰与整理时间" },
      bands.map(([a, z]) => el("div", { class: "cband", style: { left: x(a), width: x(z - a) } })), el("div", { class: "cnow", style: { left: (now / 1440 * 100) + "%" } }),
      pin("diary_generate_hour", "日记", "diary", fieldOf("diary_enabled") ? draftVal(fieldOf("diary_enabled")) : undefined),
      pin("consolidation_local_hour", "夜间整理", "cons", fieldOf("consolidation_enabled") ? draftVal(fieldOf("consolidation_enabled")) : undefined),
      el("div", { class: "cticks" }, [0, 3, 6, 9, 12, 15, 18, 21, 24].map(h => el("span", { style: { left: x(h) } }, String(h).padStart(2, "0"))))),
      el("p", { class: "note clock-note" }, "斜纹是免打扰时段：不发主动消息，回复照常。灰色标记表示这项整理没有开启。"));
}
function moneySection(c) {
  const monthly = fieldOf("monthly_limit_micro_usd"), scen = cfgFields().filter(f => isMoneyKey(f.key) && f.key !== "monthly_limit_micro_usd" && !CAP_KEYS.has(f.key));
  return el("section", { class: "sheet cfg-sec", id: "cfg-money" }, el("div", { class: "sheet-head" }, el("h2", {}, "费用与预算")),
    el("div", { class: "sheet-body" }, c.cloud_budget?.config ? cloudBudget(c.cloud_budget) : null,
      el("div", { class: "budget-block" }, el("div", { class: "bb-head" }, el("div", {}, el("h3", {}, "模型账本"),
        el("p", { class: "note" }, "所有模型调用每月的总额度。各能力自己的每日额度在“能力与开关”里调整。" + (state.currency === "CNY" ? "金额按 1 美元 = ¥" + (state.fx / 1e6).toFixed(2) + " 换算。" : ""))), el("span", { class: "chip" }, "新版本生效")),
        monthly ? fieldRow(monthly) : null,
        scen.length ? el("table", { class: "t scen" }, el("thead", {}, el("tr", {}, el("th", {}, "场景"), el("th", { class: "r" }, "额度"))),
          el("tbody", {}, scen.map(f => el("tr", { class: "static" + (state.draft.has(f.key) ? " changed" : "") }, el("td", {}, el("b", {}, f.label)), el("td", { class: "r" }, control(f), el("p", { class: "pf-err", role: "alert", hidden: true })))))) : null)));
}
function secretsSection(list) {
  return el("section", { class: "sheet cfg-sec", id: "cfg-secrets" }, el("div", { class: "sheet-head" }, el("h2", {}, "密钥"), el("span", { class: "note" }, "只能写入，任何地方都不会显示原值")),
    el("div", { class: "sheet-body" }, list.length ? el("div", { class: "pf-list" }, list.map(f => fieldRow(f))) : el("p", { class: "note" }, "没有可以在控制台替换的密钥。"),
      el("p", { class: "note mt" }, "传输令牌、主人控制令牌和读取适配器凭据在部署环境里更换。主人控制令牌同时用来加密配置版本，直接更换会导致旧版本无法解密。")));
}
function cloudBudget(policy) {
  const defs = [["daily_micro_cny", "每日云端限额", 1e6, "¥"], ["video_per_item_micro_cny", "单个视频任务上限", 1e6, "¥"], ["asr_per_item_micro_cny", "单个语音任务上限", 1e6, "¥"],
    ["usd_to_cny_micro", "美元汇率（每 1 美元）", 1e6, "¥"], ["fx_source", "汇率依据", 1, ""], ["fx_validity_seconds", "汇率有效期", 86400, "", "天"]];
  const form = el("form", { class: "cloud-grid", id: "budget-form" }, defs.map(([k, n, scale, pre, unit]) => {
    const cur = policy.config[k], value = state.budgetDraft.has(k) ? state.budgetDraft.get(k) : typeof cur === "number" ? String(+(cur / scale).toFixed(6)) : cur === "operator_planning_estimate" ? "运营估算" : cur ?? "";
    const input = el("input", { class: "input", id: "budget-" + k, value, inputmode: k === "fx_source" ? undefined : "decimal", maxlength: k === "fx_source" ? "256" : undefined,
      oninput: e => { state.budgetDraft.set(k, e.target.value); $("#budget-save").disabled = false; } });
    return el("label", { class: "field" }, el("span", {}, n), k === "fx_source" ? input : el("span", { class: "unit" + (pre ? " has-pre" : "") }, pre ? el("i", {}, pre) : null, input, unit ? el("em", {}, unit) : null));
  }));
  const save = btn("更新云端预算", () => saveBudget(policy, defs, form, save), { cls: "sm primary", disabled: !state.budgetDraft.size });
  save.id = "budget-save";
  return el("div", { class: "budget-block" }, el("div", { class: "bb-head" }, el("div", {}, el("h3", {}, "云端人民币预算"),
      el("p", { class: "note" }, "保存后立即约束之后的云调用，不生成配置版本。不会重置已有费用、预留和结果不明的调用，进行中的任务继续用开始时的上限和汇率。")), el("span", { class: "chip" }, "立即生效")),
    form, el("div", { class: "bb-acts" }, save, state.budgetDraft.size ? btn("还原", () => { state.budgetDraft.clear(); rerender(); }, { cls: "sm quiet" }) : null),
    el("div", { class: "pf-list mt" }, kv("预算版本", "v" + policy.version), kv("生效时间", fmtDate(policy.effective_at)), kv("汇率有效期至", fmtDate(policy.fx_valid_until)), kv("记账时区", policy.timezone || "未记录")));
}
function saveBudget(policy, defs, form, button) {
  const changes = Object.create(null);
  try {
    for (const [k, raw] of state.budgetDraft) {
      const def = defs.find(d => d[0] === k); if (!def) continue;
      const [, n, scale] = def; let value;
      if (k === "fx_source") { value = String(raw).trim(); if (value === "运营估算") value = "operator_planning_estimate"; if (!value || value.length > 256) throw new Error("请填写有效的汇率依据。"); }
      else if (scale === 1e6) { const m = String(raw).trim().match(/^(\d+)(?:\.(\d{0,6}))?$/); if (!m) throw new Error(n + "：请填写最多 6 位小数的数字。"); value = Number(m[1]) * 1e6 + Number((m[2] || "").padEnd(6, "0")); }
      else { const s = Number(raw) * scale; value = Math.round(s); if (raw === "" || !Number.isFinite(s) || Math.abs(s - value) > 1e-5) throw new Error(n + "：请填写能换算为整秒的时长。"); }
      if (typeof value === "number" && (!Number.isSafeInteger(value) || value < 0)) throw new Error(n + "：数值超出允许范围。");
      if (value !== policy.config[k]) changes[k] = value;
    }
  } catch (error) { formError(form, error.message); return; }
  if (!Object.keys(changes).length) { formError(form, "没有需要保存的预算修改。"); return; }
  if (state.budgetSave) return;
  state.budgetSave = true;
  perform(form, button, () => api.write("/cloud-budget", { expected_version: policy.version, changes }, "PUT", r => Number.isInteger(r.version) && r.config && typeof r.config === "object"),
    async r => { state.data.configuration.cloud_budget = r; if (Number.isSafeInteger(r.config.usd_to_cny_micro)) state.fx = r.config.usd_to_cny_micro; state.budgetDraft.clear(); rerender(); toast("已更新云端预算，立即生效"); })
    .finally(() => { state.budgetSave = null; });
}
function spyToc() {
  if (state.route !== "configuration") return;
  const secs = $$(".cfg-sec"); if (!secs.length) return;
  let cur = secs[0];
  if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4) cur = secs.at(-1);
  else for (const s of secs) if (s.getBoundingClientRect().top < 160) cur = s;
  $$(".cfg-toc a").forEach(a => a.toggleAttribute("aria-current", a.dataset.toc === cur.id));
}

/* ================= 启动与全局事件 ================= */
function openPalette() {
  const p = $("#palette"), pages = ROUTES.flatMap(([, list]) => list.map(([k, n, i]) => ({ k, n, i })));
  const list = el("ul", { id: "pal-list" });
  const draw = q => {
    const hits = pages.filter(x => x.n.includes(q));
    list.replaceChildren(...(hits.length ? hits.map((x, j) => el("li", {}, el("button", { type: "button", class: j === 0 ? "sel" : "", onclick: () => { location.hash = x.k; p.close(); } }, icon(x.i), el("span", {}, x.n))))
      : [el("li", { class: "empty" }, "没有找到“" + q + "”")]));
  };
  const input = el("input", { class: "input", placeholder: "跳转到页面", "aria-label": "跳转到页面",
    oninput: e => draw(e.target.value.trim()), onkeydown: e => { if (e.key === "Enter") $("button", list)?.click(); } });
  p.replaceChildren(input, list); draw(""); p.showModal(); input.focus();
}
const tip = () => $("#tip");
function showTip(target) {
  const lines = tipOf.get(target); if (!lines) return;
  const r = target.getBoundingClientRect(), t = tip();
  t.replaceChildren(...lines.map(([time, title, st]) => el("div", { class: "tip-line" }, el("b", {}, time + " " + title), el("span", {}, st))));
  t.hidden = false;
  const w = t.offsetWidth;
  t.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
  t.style.top = Math.max(8, r.top - t.offsetHeight - 8) + "px";
}
document.addEventListener("pointerover", e => { const m = e.target.closest?.(".mark"); if (m) showTip(m); else tip().hidden = true; });
document.addEventListener("focusin", e => { const m = e.target.closest?.(".mark"); if (m) showTip(m); else tip().hidden = true; });

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const button = $("#login-submit"), err = $("#login-err");
  if (!$("#pw").value) { err.textContent = "请输入控制台密码。"; err.hidden = false; $("#pw").focus(); return; }
  button.disabled = true; err.hidden = true;
  try {
    const value = await api.request("/session", { method: "POST", body: { password: $("#pw").value }, authenticate: false,
      validate: r => typeof r.csrf_token === "string" && Boolean(r.csrf_token) && valid(r.expires_at) });
    await acceptSession(value);
  } catch (error) { err.textContent = errorText(error); err.hidden = false; }
  finally { button.disabled = false; }
});
$("#logout").addEventListener("click", async () => {
  let failure = "";
  try { await api.write("/session", {}, "DELETE"); } catch { failure = "本地显示已清除，服务器上的退出结果没有确认。请重新登录后核对。"; }
  clearSession(failure); $("#pw").focus();
});
$("#menu").addEventListener("click", () => $("#side").classList.toggle("open"));
$("#refresh").addEventListener("click", () => loadRoute());
$("#search-open").addEventListener("click", openPalette);
$("#drawer-scrim").addEventListener("click", closeDrawer);
window.addEventListener("hashchange", () => { if (!location.hash.startsWith("#configuration")) state.cfgScrolled = false; navigate(); });
window.addEventListener("scroll", () => { $("#top").classList.toggle("scrolled", scrollY > 4); spyToc(); }, { passive: true });
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && state.session) { e.preventDefault(); openPalette(); }
  if (e.key === "Escape" && $("#drawer").classList.contains("open")) closeDrawer();
  if (e.key === "Escape" && $("#side").classList.contains("open")) $("#side").classList.remove("open");
});
window.addEventListener("offline", () => { if (state.session) { setConnection("off", "网络已断开"); notice("网络已断开，页面保留上次读取的记录。", true); } });
window.addEventListener("online", () => { if (state.session && !state.draft.size) loadRoute(); });
window.addEventListener("beforeunload", e => { if (state.draft.size || state.budgetDraft.size) { e.preventDefault(); e.returnValue = ""; } });
setInterval(updateSessionLeft, 30000);
for (const node of $$("[data-icon]")) node.replaceChildren(icon(node.dataset.icon));

async function start() {
  try { await acceptSession(await api.request("/session", { authenticate: false })); }
  catch (error) { clearSession(error.status === 401 ? "" : "暂时无法恢复会话。" + errorText(error)); }
}
start();
