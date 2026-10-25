const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function el(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key === "checked" || key === "value") element[key] = value;
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child !== undefined && child !== null && child !== false) {
      element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
  return element;
}

const PATHS = {
  overview: ["M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z"],
  activity: ["M4 6h4m4 0h8M4 12h4m4 0h8M4 18h4m4 0h8"],
  memory: ["M9 18h6m-5 3h4M8 15c-4-3-4-8 0-10s9 0 9 4c0 3-2 4-2 6v1H9v-1"],
  life: ["M12 21V10M12 14C5 15 3 10 3 5c6 0 10 3 9 9zM12 10c0-6 4-8 9-8 0 5-3 9-9 8z"],
  content: ["M3 4h6l3 2 3-2h6v15h-6l-3 2-3-2H3zM12 6v15"],
  image: ["M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM3 16l6-6 7 7m-3-3 4-4 4 4", "M15 7h.01"],
  pulse: ["M2 12h5l3-8 4 16 3-8h5"],
  settings: ["M9 3h6l1 4 4 1v8l-4 1-1 4H9l-1-4-4-1V8l4-1z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6"],
  shield: ["M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"],
  logout: ["M9 4H4v16h5m4-12 4 4-4 4M8 12h13"],
  menu: ["M4 6h16M4 12h16M4 18h16"],
  close: ["m6 6 12 12M6 18 18 6"],
  refresh: ["M20 8a8 8 0 1 0 0 8M20 3v5h-5"],
  "arrow-right": ["M4 12h16m-6-6 6 6-6 6"],
  "arrow-left": ["M20 12H4m6-6-6 6 6 6"],
  arrow: ["M6 18 18 6M7 6h11v11"],
  search: ["M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14m5 12 6 6"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18m0 4v5l3 2"],
  check: ["m5 12 4 4L19 6"],
  warning: ["m12 3 10 18H2zM12 9v5m0 3h.01"],
  wallet: ["M4 6V4h15v4H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h17V8H4m17 5h-6v4h6"],
  inbox: ["M4 4h16l2 13v4H2v-4zM2 17h6l2 2h4l2-2h6"],
  microphone: ["M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM5 10v1a7 7 0 0 0 14 0v-1M12 18v4M8 22h8"],
  account: ["M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8M4 21v-2a8 8 0 0 1 16 0v2M17 5h5M20 2v6"],
  edit: ["m15 4 5 5M4 20l5-1L21 7l-5-5L4 14z"],
  trash: ["M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7m4-7v7"],
  link: ["m9 15 6-6M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"],
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of PATHS[name] || PATHS.activity) {
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

const ROUTES = {
  overview: ["概览", "此刻与近况", "状态、费用与最近活动，放在一起看。"],
  activity: ["活动时间线", "发生过的每一步", "从任务到回执，沿着记录了解发生了什么。"],
  memories: ["记忆", "值得记得的事", "浏览有来源的记忆，确认、修订或忘记。"],
  life: ["成长与日记", "日常的积累", "查看日记、成长与主动行为留下的记录。"],
  content: ["阅读与文件", "内容与来源", "查看阅读、视频和文件任务的进度与来源。"],
  memes: ["表情素材", "理解之后，再表达", "查看来源审核、图片理解和索引的实际状态。"],
  voice: ["语音与节点", "从听见到回应", "查看节点联系、语音处理与原消息的投递记录。"],
  "account-actions": ["QQ 自主行为", "计划与实际结果", "分别查看账号计划、平台回执、资料观测与素材许可。"],
  diagnostics: ["运行诊断", "运行的细节", "检查工作进程、队列、费用和需要处理的问题。"],
  configuration: ["配置", "按需要调整", "查看生效版本，管理服务、能力与使用限额。"],
};

const STATUS = {
  READY: "可用", ATTENTION: "需要留意", ACTIVE: "已启用", ENABLED: "已启用",
  DISABLED: "未启用", UNAVAILABLE: "不可用", PAUSED: "已暂停", STOPPED: "已停止",
  RECORDED: "已记录", STALE: "记录已过期", UNOBSERVED: "尚无运行记录",
  OBSERVED: "已观测", QUEUED: "排队中", PENDING: "待处理", RUNNING: "处理中",
  RETRY_WAIT: "等待重试", SUCCEEDED: "已完成", COMPLETED: "已完成", COMMITTED: "已保存",
  SETTLED: "已结算", SENT: "已发送", ACK: "已确认送达", UNKNOWN: "结果不明",
  FAILED: "失败", ERROR: "发生错误", CANCELLED: "已取消", SUPERSEDED: "已被新输入替代",
  RECALLED: "已撤回", CONFIRMED: "已确认", HYPOTHESIS: "待确认", FORGOTTEN: "已忘记",
  RETRACTED: "已撤销", DELETED: "已删除", REDACTED: "已清除", EXPIRED: "已过期",
  PROPOSED: "待确认", REVIEW: "待审核", QUARANTINED: "已隔离", INVALID: "已失效",
  INDEXED: "已索引", FETCHED: "已取得内容", DISCOVERED: "已发现", WATCHED: "已有分析记录",
  PARTIAL: "部分完成", RESERVED: "已预留", WAITING: "等待中", NOT_CONFIGURED: "未配置",
  COMPLETE: "已完成", DELIVERY_UNCERTAIN: "投递结果不明", BLOCKED: "受阻", DRAFT: "草稿",
  ONLINE: "近期有联系", OFFLINE: "联系记录已过期", BUSY: "有未结束任务",
  TRANSCRIBED: "转写已完成", LEASED: "已领取", SENDING: "已开始发送",
  UNVERIFIED: "需要核对", FRESH: "近期已观测", REVIEWED: "审核有效", REVOKED: "已撤销",
  AVAILABLE: "限频未阻止", REJECTED: "未通过", SKIPPED: "本轮保持不变", PLANNING: "正在规划",
  MATCHED: "与预期一致", CHANGED: "与预期不同",
};
const KINDS = {
  memory: "记忆", diary: "日记", life: "生活记录", growth: "成长", job: "任务",
  delivery: "消息投递", model: "模型调用", runtime: "运行状态", reading: "内容阅读",
  content: "内容任务", meme: "表情素材", caption: "图片理解", budget: "费用与预算",
  audit: "操作记录", configuration: "配置修改", file: "文件", video: "视频", voice: "语音",
  jobs: "任务", outbox: "消息投递", meme_caption: "表情理解", qq_image: "主人图片",
  link_image: "文章图片", link: "分享链接", goal: "目标", reflection: "反思",
  core: "运行记录", console: "主人操作", "cloud-budget": "费用与预算",
  voice_node: "语音节点", voice_job: "语音任务", account_plan: "账号计划",
  account_profile: "资料观测", account_asset: "头像素材",
};
const DELIVERY_ACTIONS = {
  "qq.private.reply": "私聊回复", "qq.private.proactive": "主动分享", "qq.private.poke": "拍一拍",
  "qzone.publish": "QQ空间发布", "qq.profile.nickname": "昵称更新",
  "qq.profile.signature": "签名更新", "qq.profile.avatar": "头像更新",
};
const ACTIVITY_TITLES = {
  "event.accepted": "已接收一条输入", "console.login": "主人登录控制台",
  "console.operation": "已记录控制台操作", "configuration.saved": "已保存新的配置版本",
  "cloud_budget.bound": "已建立云端预算记录", "cloud_budget.configured": "已更新云端预算",
  "cloud_budget.native_settled": "已同步一笔已结算费用", "model.not_dispatched": "模型请求未派发",
  "memory.conflict": "发现需要核对的记忆", "memory.control": "已记录记忆管理操作",
  "memory.recall_invalid": "记忆检索结果未通过核验", "reading.owner_requested": "已接收主人阅读请求",
  "reply.superseded": "回复已由新的输入接续", "diary.recompute_requested": "已请求重新整理日记",
  "diary.committed": "日记已保存", "diary.failed": "日记整理失败", "diary.cancelled": "日记整理已取消",
};
function displayTitle(item) {
  return (["activity", "overview"].includes(state.route) && ACTIVITY_TITLES[item.title]) || item.title || "未命名记录";
}
const CAPABILITIES = {
  "memory.manage": "记忆管理", "memory.recall": "记忆检索", diary: "日记整理",
  consolidation: "记忆整理", reflection: "反思", goals: "目标与承诺", wake: "自主活动调度",
  affect: "互动状态", expression: "表情回应", expression_learning: "表达习惯学习",
  proactive: "主动私聊", social: "内容分享判断",
  "reading.bilibili": "B站内容读取", "reading.web": "网页阅读", "reading.heybox": "小黑盒内容读取",
  exploration: "内容发现", "bilibili.video": "B站画面分析", "model.understand_video": "本轮视频理解",
  "qq.files": "私人文件", "qq.voice.asr": "语音转写", "qq.voice.tts": "语音回复",
  "qq.poke": "拍一拍回应", "qq.poke.native": "回拍一下",
  "qq.account.qzone_publish": "QQ 空间发布", "qq.account.nickname": "改昵称",
  "qq.account.signature": "改签名", "qq.account.avatar": "改头像", "qq.profile.observe": "资料观测",
  "qq.images": "私聊图片理解", "qq.animation": "私聊动图理解", "qq.face_names": "QQ 表情名称",
  "qq.face_images": "QQ 表情图像", "unicode.emoji": "Unicode 表情", "memes.semantic": "表情语义理解",
  "memes.catalog": "表情目录", "memes.community_import": "社区表情获取", "memes.search": "表情搜索",
  executive: "执行任务", "media.voice": "网页语音会话", "media.camera": "摄像头", "media.android": "安卓观察",
};
// Release removal point: this flag disables all implementation notes. These are
// reviewed backend execution coverage, separate from UI gaps and runtime authorization.
const SHOW_IMPLEMENTATION_STATUS = true;
const CAPABILITY_IMPLEMENTATION = {
  "memory.manage": ["已实现", "可查询、确认、纠正和忘记记忆。"],
  "memory.recall": ["已实现", "已有来源约束的检索；控制台尚无原话摘录和召回解释。"],
  diary: ["已实现", "已有日记读取、纠正、忘记与暂停；不包含未来睡眠阶段或梦境。"],
  consolidation: ["已实现", "已有夜间整理；尚无 N1/N2/N3 阶段观察。"],
  reflection: ["已实现", "已有来源支持的反思；控制台尚不能接受或拒绝建议。"],
  goals: ["已实现", "已有目标与承诺；控制台目前只读，尚无逐步执行视图。"],
  wake: ["已实现", "已有受限调度与暂停；尚未接入完整自主决策观察。"],
  affect: ["已实现", "已有反馈与衰减；控制台尚无来源、重置和撤销视图。"],
  expression: ["已实现", "已有受限表情选择及投递流程；实际表达效果仍需体验验证。"],
  expression_learning: ["已实现", "已有有来源的表达偏好；尚无跨日变化与证据详情。"],
  proactive: ["已实现", "已有主动私聊和暂停；尚无逐轮发送或跳过的决策视图。"],
  social: ["已实现", "已有来源支持的分享候选；尚无统一表达意图视图。"],
  "reading.web": ["已实现", "已有授权网站读取；控制台只读任务，授权由部署保管。"],
  "reading.bilibili": ["已实现", "已有 B站读取回执；控制台尚无读取授权管理。"],
  "reading.heybox": ["已实现", "已有小黑盒读取回执；控制台尚无读取授权管理。"],
  exploration: ["已实现", "已有内容发现；控制台尚无逐步探索与来源授权操作。"],
  "bilibili.video": ["已实现", "已有受限视频分析；控制台尚无采样范围详情。"],
  "model.understand_video": ["已实现", "仅本轮实际开放工具时可调用；控制台尚无工具回执详情。"],
  "qq.files": ["已实现", "已有私人文件解析和任务记录；尚无页级证据详情。"],
  "qq.images": ["已实现", "已有认证图片理解；控制台尚无图像来源详情。"],
  "qq.animation": ["已实现", "已有受限动图理解；控制台尚无帧级证据详情。"],
  "qq.voice.asr": ["已实现", "已有语音转写与节点状态；控制台不提供字幕或音频回放。"],
  "qq.voice.tts": ["已实现", "已有语音合成及原投递回执；控制台不提供音频回放。"],
  "qq.poke": ["已实现", "已有受限拍一拍回应；控制台尚无批次详情。"],
  "qq.poke.native": ["已实现", "已有独立许可与原生回拍回执；控制台尚无操作详情。"],
  "qq.account.qzone_publish": ["已实现", "已有独立授权发布与回执；控制台尚不能审核草稿。"],
  "qq.account.nickname": ["已实现", "已有独立许可与回执；控制台尚不能允许或拒绝计划。"],
  "qq.account.signature": ["已实现", "已有独立许可与回执；控制台尚不能允许或拒绝计划。"],
  "qq.account.avatar": ["已实现", "已有审核素材与回执；控制台尚无头像预览或计划决定。"],
  "qq.profile.observe": ["已实现", "已有资料观测；缓存观测不能证明网络刷新或修改成功。"],
  "unicode.emoji": ["已实现", "保留输入中的 Unicode 表情原文。"],
  "qq.face_names": ["已实现", "在富媒体输入范围内识别 QQ 原生表情名称。"],
  "qq.face_images": ["已实现", "在图片许可内接收认证表情图；是否看过以本轮回执为准。"],
  "memes.catalog": ["已实现", "已有审核素材目录；控制台尚无编辑、上传或隔离操作。"],
  "memes.community_import": ["已实现", "已有受限素材导入；控制台尚无素材审核操作。"],
  "memes.search": ["已实现", "已有配置搜索服务的发现路径；控制台尚无搜索操作。"],
  "memes.semantic": ["已实现", "已有带来源的理解任务；控制台目前只读任务记录。"],
  "media.voice": ["已实现", "已有独立媒体会话；控制台尚无启动和关闭入口。"],
  "media.camera": ["已实现", "已有受限摄像头观察；控制台尚无会话控制。"],
  "media.android": ["已实现", "已有受限设备观察；不代表已实现设备操作。"],
  executive: ["已实现", "已有独立审批执行器；控制台尚无任务提交、审批和撤销入口。"],
  // P1–P4 registration points: displayed only if a future backend publishes the
  // same capability ID. Update this map with that slice's source/test receipt.
  "conversation.evaluation": ["待实现", "自然度对照与有界评价尚未集成到当前版本。"],
  "memory.tiers": ["待实现", "热温冷逻辑分层尚未集成到当前版本。"],
  "consolidation.stages": ["待实现", "N1/N2/N3 阶段尚未集成到当前版本。"],
  "life.decisions": ["待实现", "完整自主候选和执行反馈尚未集成到当前版本。"],
  "dream.rem": ["待实现", "默认关闭的虚构梦境尚未集成到当前版本。"],
  "social.intent": ["待实现", "统一表达意图尚未集成到当前版本。"],
};
function implementationNote(id) {
  const entry = CAPABILITY_IMPLEMENTATION[id];
  return SHOW_IMPLEMENTATION_STATUS && entry
    ? el("small", { class: "panel-note", "data-implementation": id, title: entry[1] }, " · 后端：" + entry[0])
    : null;
}
const GROUPS = {
  model: "模型与服务", content: "阅读与文件", expression: "表达与表情",
  autonomy: "自主行为", memory: "记忆与日记", system: "运行与域名",
  budget: "费用与预算",
};
const DETAIL_LABELS = {
  id: "记录编号", job_id: "任务编号", event_id: "输入编号", memory_id: "记忆编号",
  kind: "类型", state: "状态", status: "状态", created_at: "创建时间", updated_at: "更新时间",
  occurred_at: "发生时间", completed_at: "完成时间", source: "来源", sources: "来源证据",
  source_url: "来源链接", url: "链接", source_page: "来源页面", evidence: "关联证据",
  reason: "原因", error_code: "原因代码", last_error_code: "最近原因", reason_codes: "状态原因",
  content: "记忆内容", body_md: "日记正文", text: "内容", subject: "主体", fact_key: "记忆类别",
  confidence: "置信度", local_date: "日期", day: "日期", timezone: "时区", version: "版本",
  license: "使用许可", usage_basis: "使用依据", asset_sha256: "素材摘要", source_sha256: "来源摘要",
  caption: "图片理解", description: "描述", tags: "标签", visible_text: "图中文字", tone: "语气",
  suitable: "适用场景", avoid: "避免场景", source_evidence: "来源凭证", intent: "表达意图",
  attempts: "尝试次数", lease_until: "领取有效期", queue: "队列", worker: "工作进程",
  input_tokens: "输入用量", output_tokens: "输出用量", usage: "用量", scope: "范围",
  model: "模型", provider: "服务", state_reason: "状态说明", runtime_observation: "运行观测",
  enabled: "是否启用", active: "是否生效", target: "对象", title: "标题", summary: "摘要",
  source_event_ids: "来源输入", evidence_count: "来源数量", duration_ms: "耗时（毫秒）",
  evidence_ids: "来源输入", needs_review: "需要核对", memory_kind: "记忆类型", entity_id: "关联对象",
  trace_id: "关联记录", owner_event_id: "主人输入", output_event_id: "输出记录",
  read_request_id: "读取请求", usage_request_id: "用量记录", reserved_bytes: "预留空间（字节）",
  count: "数量", billed_micro_usd: "已计费用量（微美元）", commitment_state: "承诺状态",
  action_type: "投递类型",
  last_seen_at: "最近联系", resource_state: "上次资源状态", active_job_id: "关联任务",
  node_id: "执行节点", capabilities: "已配置能力", limits: "资源上限", operation: "处理类型",
  task_state: "处理状态", partial: "音频覆盖不完整", input_bytes: "输入大小",
  result_bytes: "结果大小", input_duration_seconds: "输入时长", delivery_state: "原消息投递",
  message_id: "原消息编号", deadline_at: "处理截止", result_expires_at: "结果有效期",
  expected_manifest_sha256: "预期配置摘要", verified_manifest_sha256: "最近任务核验摘要",
  manifest_verified_at: "最近任务核验时间", manifest_state: "配置核验",
  plan_state: "计划状态", receipt_state: "平台回执", native_retcode: "平台返回码",
  resource_id: "平台内容编号", receipt_sha256: "回执摘要", dispatched_at: "开始发送时间",
  expires_at: "有效期至", revoked_at: "撤销时间", license_label: "使用许可",
  usage_conditions: "使用条件", purpose: "审核用途", dimensions: "图片尺寸", size_bytes: "素材大小",
  review_receipt_sha256: "审核凭证摘要", field: "资料项目", baseline_value: "最近观测内容",
  observed_at: "观测时间", freshness_seconds: "观测有效时长", freshness_state: "观测新鲜度",
  last_action_at: "最近相关操作", level: "读取范围", part: "内容部分",
};
const SECRET_KEY = /password|cookie|authorization|api_key|access_token|refresh_token|csrf|secret|credential/i;
const state = {
  session: null, epoch: 0, generation: 0, route: "overview", data: null,
  selection: null, query: "", filter: "all", controller: null,
  group: null, dirty: false, configDraft: new Map(), budgetDraft: new Map(), expandedCapabilities: false,
  configSave: null, budgetSave: null, deliverySaves: new Map(),
  updatedAt: null, toastTimer: null, searchTimer: null,
};

class ApiError extends Error {
  constructor(code, status = 0, fields = [], uncertain = false) {
    super(code);
    this.code = code;
    this.status = status;
    this.fields = fields;
    this.uncertain = uncertain;
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
        if (!globalThis.crypto?.randomUUID || !globalThis.crypto?.subtle) {
          throw new ApiError("CONSOLE_HTTPS_REQUIRED");
        }
        const bytes = new TextEncoder().encode(JSON.stringify([method, path, body]));
        const hash = await crypto.subtle.digest("SHA-256", bytes);
        attempt = [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, "0")).join("");
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
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new ApiError("CONSOLE_RESPONSE_INVALID", response.status, [], mutation);
      }
      if (validate && !validate(value)) throw new ApiError("CONSOLE_RESPONSE_INVALID", response.status, [], mutation);
      if (attempt) this.attempts.delete(attempt);
      return value;
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      if (error instanceof ApiError) throw error;
      throw new ApiError(controller.signal.aborted ? "CONSOLE_TIMEOUT" : "CONSOLE_NETWORK_ERROR",
        0, [], mutation);
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  },
  list(route, { before, query, review, signal } = {}) {
    const params = new URLSearchParams();
    if (route === "activity") {
      params.set("limit", "30");
      if (before !== undefined && before !== null) params.set("before", String(before));
    }
    if (route === "memories") {
      params.set("limit", "30");
      params.set("review", String(Boolean(review)));
      if (query) params.set("q", query);
    }
    return this.request("/" + route + (params.size ? "?" + params : ""), { signal });
  },
  write(path, body, method = "POST", validate) { return this.request(path, { method, body, validate }); },
};

function errorText(error) {
  const messages = {
    CONSOLE_NETWORK_ERROR: "连接暂时中断，请检查网络后重试。",
    CONSOLE_TIMEOUT: "等待服务器响应超时，请稍后重试。",
    CONSOLE_RESPONSE_INVALID: "服务器返回的数据不完整，请刷新核对。",
    CONSOLE_HTTPS_REQUIRED: "请通过 HTTPS 访问控制台后再操作。",
  };
  let message = messages[error.code];
  if (!message && error.status === 401) message = "密码无效或会话已过期，请重新登录。";
  if (!message && error.status === 403) message = "当前操作未获准，请刷新会话与状态后核对。";
  if (!message && error.status === 409) message = "记录或配置已发生变化，请刷新后重新核对。";
  if (!message && error.status === 422) message = "部分填写内容不符合要求，请检查后重试。";
  if (!message && error.status === 429) message = "操作过于频繁或额度不足，请稍后核对状态。";
  if (!message && error.status === 503) message = "当前服务暂不可用，请查看运行诊断。";
  if (!message) message = "操作未完成，请稍后重试或查看运行诊断。";
  if (error.uncertain) message = "操作结果尚未确认，请先刷新核对。再次提交相同内容会沿用原操作编号。";
  const code = typeof error.code === "string" ? error.code : "CONSOLE_REQUEST_FAILED";
  return message + "（" + code + "）";
}

function formatDate(value, { timeOnly = false } = {}) {
  if (!value || !Number.isFinite(Date.parse(value))) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", timeOnly
    ? { hour: "2-digit", minute: "2-digit", hour12: false }
    : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
    .format(new Date(value));
}
function count(value) { return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—"; }
function money(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(value / 1e6)
    : "—";
}
function tone(value) {
  const status = String(value || "").toUpperCase();
  if (["FAILED", "ERROR", "UNKNOWN", "ATTENTION", "INVALID", "DELIVERY_UNCERTAIN", "BLOCKED", "UNVERIFIED", "QUARANTINED"].includes(status)) return "danger";
  if (["QUEUED", "PENDING", "RETRY_WAIT", "HYPOTHESIS", "PROPOSED", "REVIEW", "STALE", "PARTIAL", "RESERVED", "OFFLINE", "SENDING"].includes(status)) return "pending";
  if (["SUCCEEDED", "COMPLETED", "COMMITTED", "CONFIRMED", "SETTLED", "SENT", "ACK", "INDEXED", "OBSERVED", "FRESH", "REVIEWED", "TRANSCRIBED"].includes(status)) return "success";
  if (["READY", "ACTIVE", "ENABLED", "RUNNING", "RECORDED", "ONLINE", "BUSY", "LEASED"].includes(status)) return "active";
  return "";
}
function pill(value, label) { return el("span", { class: "status-pill " + tone(value) }, label || STATUS[value] || value || "未记录"); }
function action(text, onClick, { style = "", symbol, small = false } = {}) {
  return el("button", { type: "button", class: "button " + style + (small ? " small" : ""), onclick: onClick },
    symbol ? icon(symbol) : null, text);
}
function link(text, route) { return el("a", { href: "#" + route, class: "text-link" }, text, icon("arrow-right")); }
function panel(title, body, right = null, className = "") {
  return el("section", { class: "panel " + className },
    el("div", { class: "panel-header" }, el("h2", {}, title), right), body);
}
function safeUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function safeValue(value, depth = 0) {
  if (depth > 4) return "…";
  if (value === null || value === undefined) return "未记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value !== "object") return String(value).slice(0, 8000);
  if (Array.isArray(value)) return value.slice(0, 30).map(item => safeValue(item, depth + 1)).join("\n");
  return Object.entries(value).filter(([key]) => !SECRET_KEY.test(key)).slice(0, 30)
    .map(([key, item]) => (DETAIL_LABELS[key] || key) + "：" + safeValue(item, depth + 1)).join("\n");
}

function setConnection(kind, text) {
  const target = $("#connection");
  target.className = "connection " + kind;
  target.lastElementChild.textContent = text;
}
function notice(text, kind = "", retry = false) {
  const target = $("#notice");
  target.className = "notice " + kind;
  target.hidden = !text;
  target.replaceChildren();
  if (text) target.append(icon(kind === "error" ? "warning" : "pulse"), el("p", {}, text));
  if (retry) target.append(action("重试", () => loadRoute(), { small: true }));
}
function toast(text) {
  clearTimeout(state.toastTimer);
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  state.toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 5500);
}
function setMenu(open) {
  const mobile = matchMedia("(max-width: 700px)").matches;
  $("#sidebar").classList.toggle("open", open && mobile);
  $("#nav-scrim").hidden = !open || !mobile;
  $("#nav-toggle").setAttribute("aria-expanded", String(open && mobile));
  $("#workspace").inert = open && mobile;
  if (open && mobile) $("#nav-close").focus();
}
function clearSession(message = "") {
  state.epoch += 1;
  state.generation += 1;
  state.controller?.abort();
  state.session = null;
  state.data = null;
  state.selection = null;
  state.configDraft.clear();
  state.budgetDraft.clear();
  state.configSave = null;
  state.budgetSave = null;
  state.deliverySaves.clear();
  clearTimeout(state.searchTimer);
  state.dirty = false;
  state.query = "";
  api.attempts.clear();
  setMenu(false);
  $("#action-dialog").close();
  $("#dialog-content").replaceChildren();
  $("#view").replaceChildren();
  $("#notice").replaceChildren();
  $("#toast").hidden = true;
  $("#app").hidden = true;
  $("#boot").hidden = true;
  $("#login").hidden = false;
  $("#password").value = "";
  $("#login-error").hidden = !message;
  $("#login-error").textContent = message;
}
function acceptSession(value) {
  if (typeof value.csrf_token !== "string" || !value.csrf_token || !Number.isFinite(Date.parse(value.expires_at))) {
    throw new ApiError("CONSOLE_RESPONSE_INVALID");
  }
  state.session = { csrf_token: value.csrf_token, expires_at: value.expires_at };
  $("#session-expiry").textContent = formatDate(value.expires_at) + " 到期";
  $("#login").hidden = true;
  $("#boot").hidden = true;
  $("#app").hidden = false;
  $("#password").value = "";
  return navigate();
}

function skeleton() {
  return el("div", { "aria-label": "正在加载页面" },
    el("div", { class: "metrics" }, [1, 2, 3].map(() => el("div", { class: "panel metric" },
      el("span", { class: "skeleton line short" }), el("span", { class: "skeleton number" }), el("span", { class: "skeleton line" })))),
    panel("正在读取记录", el("div", {}, [1, 2, 3].map(() => el("div", { class: "skeleton-row" },
      el("span", { class: "skeleton line" }), el("span", { class: "skeleton line short" }))))));
}
function empty(title, text, { symbol = "inbox", error = false, retry = false } = {}) {
  return el("div", { class: "empty-state " + (error ? "error-state" : ""), role: error ? "alert" : "status" },
    el("span", { class: "empty-symbol" }, icon(symbol)), el("h2", {}, title), el("p", {}, text),
    retry ? action("重新加载", () => loadRoute(), { symbol: "refresh" }) : null);
}

async function navigate() {
  const route = location.hash.slice(1).split("?")[0] || "overview";
  if (!ROUTES[route]) { location.hash = "overview"; return; }
  if (route !== state.route && state.dirty && !confirm("配置有尚未保存的修改。要放弃修改并离开吗？")) {
    history.replaceState(null, "", "#" + state.route);
    return;
  }
  if (route !== state.route) {
    clearTimeout(state.searchTimer);
    state.query = "";
    state.filter = "all";
    state.selection = null;
    state.configDraft.clear();
    state.budgetDraft.clear();
    state.dirty = false;
  }
  state.route = route;
  const closeMobileMenu = $("#sidebar").classList.contains("open");
  setMenu(false);
  const [title, eyebrow, description] = ROUTES[route];
  $("#location-title").textContent = title;
  $("#page-title").textContent = title;
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-description").textContent = description;
  document.title = title + " · Companion 主人控制台";
  if (closeMobileMenu) $("#main").focus();
  for (const anchor of $$("[data-route]")) {
    if (anchor.dataset.route === route) anchor.setAttribute("aria-current", "page");
    else anchor.removeAttribute("aria-current");
  }
  if (state.session) await loadRoute({ reset: true });
}

async function loadRoute({ reset = false, more = false } = {}) {
  if (!state.session) return;
  if (!reset && state.route === "configuration" && state.dirty) {
    if (!confirm("刷新会丢弃尚未保存的配置修改，继续吗？")) return;
    state.configDraft.clear(); state.budgetDraft.clear(); state.dirty = false;
  }
  const generation = ++state.generation;
  const epoch = state.epoch;
  state.controller?.abort();
  state.controller = new AbortController();
  const signal = state.controller.signal;
  const route = state.route;
  if (reset) state.data = null;
  if (!state.data) $("#view").replaceChildren(skeleton());
  $("#view").setAttribute("aria-busy", "true");
  $("#refresh").disabled = true;
  setConnection("loading", "正在同步");
  notice("");
  try {
    const before = more ? state.data?.next_cursor : undefined;
    let value = await api.list(route, { before, query: state.query, review: state.filter === "review", signal });
    if (route === "overview") {
      const recent = await Promise.allSettled([
        api.list("activity", { signal }), api.list("diagnostics", { signal }),
      ]);
      value = { ...value, recent: recent[0].status === "fulfilled" ? recent[0].value : null,
        diagnostics: recent[1].status === "fulfilled" ? recent[1].value : null };
    } else if (route !== "configuration" && (!Array.isArray(value.items) || value.items.some(item =>
      !item || !["string", "number"].includes(typeof item.id) || typeof item.title !== "string" || typeof item.status !== "string"))) {
      throw new ApiError("CONSOLE_RESPONSE_INVALID");
    }
    if (generation !== state.generation || epoch !== state.epoch || !state.session) return;
    if (more) {
      const unique = new Map((state.data?.items || []).map(item => [String(item.id), item]));
      for (const item of value.items) unique.set(String(item.id), item);
      value.items = [...unique.values()];
    }
    state.data = value;
    state.updatedAt = new Date().toISOString();
    render();
    setConnection("online", "已连接控制台");
    $("#updated-at").textContent = "本页读取于 " + formatDate(state.updatedAt, { timeOnly: true });
    if (!navigator.onLine) notice("当前网络已断开，页面保留上次读取的记录。", "", true);
  } catch (error) {
    if (error.name === "AbortError" || generation !== state.generation || epoch !== state.epoch || !state.session) return;
    setConnection(navigator.onLine ? "error" : "offline", navigator.onLine ? "同步失败" : "网络已断开");
    if (state.data) notice("数据尚未更新。" + errorText(error), "error", true);
    else $("#view").replaceChildren(el("section", { class: "panel" },
      empty(navigator.onLine ? "暂时无法读取" : "网络已断开", errorText(error), { error: true, retry: true, symbol: "warning" })));
  } finally {
    if (generation === state.generation) {
      $("#view").setAttribute("aria-busy", "false");
      $("#refresh").disabled = false;
      if ($("#load-more")) $("#load-more").disabled = false;
    }
  }
}

function render() {
  if (!state.session || !state.data) return;
  const active = document.activeElement;
  const restoreSearch = active?.id === "record-search";
  const caret = restoreSearch ? active.selectionStart : null;
  let content;
  if (state.route === "overview") content = renderOverview(state.data);
  else if (state.route === "configuration") content = renderConfiguration(state.data);
  else content = renderCollection(state.data);
  $("#view").replaceChildren(content);
  if (restoreSearch && $("#record-search")) {
    $("#record-search").focus();
    $("#record-search").setSelectionRange(caret, caret);
  }
}

function metric(title, value, foot, symbol, unit = "") {
  return el("section", { class: "panel metric" },
    el("div", { class: "metric-top" }, title, el("span", { class: "metric-icon" }, icon(symbol))),
    el("div", { class: "metric-value" }, count(value), unit ? el("small", {}, unit) : null),
    el("p", { class: "metric-foot" }, foot));
}

function renderOverview(data) {
  const runtime = data.runtime || {}, counts = data.counts || {}, budget = data.budget || {};
  const worker = runtime.worker || {};
  const ready = runtime.state === "READY";
  const statusText = runtime.state ? (ready ? "当前运行状态正常" : "有些状态需要留意") : "尚未取得运行状态";
  const workerText = ["WAITING_AFTER_POLL", "WAITING_AFTER_MAINTENANCE"].includes(worker.stage)
    ? "上次完成后进入等待（当前存活未验证）"
    : worker.state === "RECORDED" ? "最近有运行记录" : STATUS[worker.state] || "尚无工作进程记录";
  const focus = el("section", { class: "focus-card" }, el("span", { class: "focus-symbol" }, icon(ready ? "pulse" : "clock")),
    el("div", { class: "focus-copy" }, el("span", { class: "eyebrow" }, "当前状态"), el("h2", {}, statusText),
      el("p", {}, workerText + "。" + (runtime.mode === "live" ? "当前使用正式运行模式。" : runtime.mode === "demo" ? "当前使用演示运行模式。" : "运行模式尚未记录。"))),
    el("div", { class: "focus-side" }, "状态观测时间", el("strong", {}, formatDate(data.observed_at))));
  const recentItems = Array.isArray(data.recent?.items) ? data.recent.items.slice(0, 4) : [];
  const recentBody = data.recent === null
    ? empty("活动暂时未读取", "其他状态已显示，可以稍后刷新活动记录。", { symbol: "activity" })
    : recentItems.length ? el("ul", { class: "mini-list" }, recentItems.map(item => el("li", {},
      el("div", { class: "mini-row" }, el("span", { class: "mini-icon" }, icon(kindIcon(item.kind))),
        el("div", { class: "mini-row-main" }, el("p", { class: "mini-row-title", title: item.title }, displayTitle(item)),
          el("p", { class: "mini-row-meta" }, el("time", {}, formatDate(item.occurred_at)), el("span", {}, KINDS[item.kind] || item.kind))), pill(item.status)))))
      : empty("这里还很安静", "发生新的任务或操作后，记录会出现在这里。", { symbol: "activity" });
  const capabilities = Array.isArray(data.capabilities?.capabilities) ? data.capabilities.capabilities : [];
  const visibleCapabilities = state.expandedCapabilities ? capabilities : capabilities.slice(0, 6);
  const capabilitiesBody = visibleCapabilities.length ? el("div", { class: "panel-body" },
    visibleCapabilities.map(cap => el("div", { class: "key-value" },
      el("span", { title: typeof cap.summary === "string" ? cap.summary : "" }, CAPABILITIES[cap.id] || cap.label || cap.id,
        implementationNote(cap.id)), pill(cap.state))),
    el("p", { class: "panel-note" }, "可用表示可接纳任务，不代表已经执行或远端在线。",
      SHOW_IMPLEMENTATION_STATUS ? " 后端标记说明代码接入范围；授权、当前可用状态和真人验收分别核对。停留在标记上可查看范围与界面缺口。" : null))
    : empty("尚无能力状态", "能力状态会随服务接入和运行配置更新。", { symbol: "pulse" });
  const problems = Array.isArray(data.diagnostics?.items)
    ? data.diagnostics.items.filter(item => ["danger", "pending"].includes(tone(item.status))) : [];
  const attention = counts.unknown_calls > 0
    ? el("div", { class: "attention" }, icon("warning"), el("div", {}, el("h3", {}, count(counts.unknown_calls) + " 次调用的结果尚未确认"),
      el("p", {}, "相关费用仍需核对，请先查看诊断记录。")))
    : problems.length ? el("div", { class: "attention" }, icon("warning"), el("div", {}, el("h3", {}, problems[0].title), el("p", {}, problems[0].summary))) : null;
  return el("div", {}, focus,
    el("div", { class: "metrics" }, metric("待处理任务", counts.pending_jobs, "当前队列中的任务", "inbox"),
      metric("待投递消息", counts.pending_deliveries, "等待后续投递处理", "activity"),
      metric("待确认记忆", counts.review_memories, "等待主人核对", "memory")),
    el("div", { class: "grid columns-two" }, el("div", { class: "stack" },
      panel("最近发生", recentBody, link("查看时间线", "activity")),
      panel("能力状态", capabilitiesBody, capabilities.length > 6 ? action(state.expandedCapabilities ? "收起" : "查看全部", () => {
        state.expandedCapabilities = !state.expandedCapabilities; render();
      }, { small: true }) : null)),
    el("div", { class: "stack" }, renderBudget(budget), attention,
      panel("运行记录", el("div", { class: "panel-body" },
        el("div", { class: "key-value" }, el("span", {}, "工作进程"), pill(worker.state)),
        el("div", { class: "key-value" }, el("span", {}, "运行模式"), el("span", {}, runtime.mode === "live" ? "正式运行" : runtime.mode === "demo" ? "演示模式" : "未记录")),
        el("p", { class: "panel-note" }, "工作进程状态来自已记录的观测。")), link("查看诊断", "diagnostics")))));
}

function renderBudget(budget) {
  const known = budget.currency === "CNY";
  const value = key => known ? money(budget[key]) : "—";
  const restored = budget.restore_reconciliation_required === true || budget.restored_history_may_be_incomplete === true;
  const reconcile = restored || budget.reconciliation_required === true;
  const limit = budget.daily_limit_micro_cny;
  const committed = typeof budget.used_micro_cny === "number" ? budget.used_micro_cny : null;
  const bar = !reconcile && known && typeof limit === "number" && limit > 0 && committed !== null
    ? el("progress", { max: limit, value: Math.max(0, Math.min(limit, committed)), "aria-label": "已结算、预留与结果不明费用占每日限额" }) : null;
  const settingsLink = link("预算设置", "configuration");
  settingsLink.addEventListener("click", () => { state.group = "budget"; });
  return panel("今日云端费用", el("div", { class: "panel-body" },
    reconcile ? el("p", { class: "form-error", role: "alert" },
      (restored ? "备份恢复后的费用记录可能不完整" : "费用记录需要进一步核对")
      + (budget.admission_blocked === true ? "，云调用已暂停" : "") + "，需核对独立账本。") : null,
    el("p", { class: "eyebrow" }, reconcile && budget.admission_blocked ? "暂停期间可用额度" : "当前剩余额度"),
    el("div", { class: "budget-value" }, value("remaining_micro_cny")),
    el("p", { class: "budget-caption" }, "每日限额 " + value("daily_limit_micro_cny")), bar,
    el("div", { class: "budget-legend" }, el("span", {}, reconcile ? "已记录结算" : "已结算", el("b", {}, value("settled_micro_cny"))),
      el("span", {}, reconcile ? "已记录预留" : "已预留", el("b", {}, value("reserved_micro_cny")))),
    el("div", { class: "budget-rows" }, el("div", { class: "key-value" }, el("span", {}, "结果不明的费用"), el("span", {}, value("unknown_micro_cny")))),
    budget.over_limit ? el("p", { class: "form-error" }, "当前总占用已超出限额，后续预留受限。") : null,
    budget.fx_expired ? el("p", { class: "form-error" }, "汇率配置已过期，请在费用与预算中核对。") : null,
    el("p", { class: "panel-note" }, known ? "按配置费率与汇率核算，不代表服务商最终账单或费用上限。" : "费用记录尚未提供可确认的人民币口径。")), settingsLink);
}

function kindIcon(kind) {
  const name = String(kind || "");
  if (/voice/.test(name)) return "microphone";
  if (/account/.test(name)) return name === "account_asset" ? "image" : "account";
  if (/memory/.test(name)) return "memory";
  if (/diary|life|growth|reflection/.test(name)) return "life";
  if (/meme|caption|image/.test(name)) return "image";
  if (/read|content|file|video/.test(name)) return "content";
  if (/config/.test(name)) return "settings";
  if (/budget|usage|model/.test(name)) return "wallet";
  return "activity";
}
function filteredItems() {
  let items = Array.isArray(state.data?.items) ? state.data.items : [];
  if (state.route === "account-actions") {
    const priority = { account_plan: 0, account_profile: 1, account_asset: 2 };
    items = [...items].sort((left, right) => (priority[left.kind] ?? 3) - (priority[right.kind] ?? 3));
  }
  if (state.route !== "memories" && state.query.trim()) {
    const query = state.query.trim().toLocaleLowerCase();
    items = items.filter(item => [displayTitle(item), item.title, item.summary, item.kind, item.id].join(" ").toLocaleLowerCase().includes(query));
  }
  if (state.filter === "attention") items = items.filter(item => ["pending", "danger"].includes(tone(item.status)));
  if (state.filter === "completed") items = items.filter(item => tone(item.status) === "success");
  return items;
}
function mediaRoute() { return state.route === "voice" || state.route === "account-actions"; }
function itemStatusLabel(item) {
  return item.detail?.state_label || (item.kind === "account_profile" && item.status === "UNOBSERVED" ? "尚无资料观测" : null);
}
function duration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "未记录";
  if (seconds >= 86400 && seconds % 86400 === 0) return seconds / 86400 + " 天";
  if (seconds >= 3600 && seconds % 3600 === 0) return seconds / 3600 + " 小时";
  if (seconds >= 60 && seconds % 60 === 0) return seconds / 60 + " 分钟";
  return seconds.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + " 秒";
}
function bytes(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return value >= 1e6 ? (value / 1e6).toFixed(1) + " MB" : value >= 1000 ? (value / 1000).toFixed(1) + " KB" : value + " 字节";
}
function renderMediaOverview() {
  const meta = state.data?.meta;
  if (!meta || typeof meta !== "object") return null;
  const voice = state.route === "voice";
  const metrics = voice
    ? [metric("近期联系", meta.nodes_recent, duration(meta.freshness_seconds) + "内的联系记录", "microphone", "/ " + count(meta.nodes_total)),
      metric("等待与处理中", meta.pending_jobs, "以节点任务记录为准", "clock"),
      metric("投递待核对", meta.uncertain_deliveries, "处理完成不等于已送达", "inbox")]
    : [metric("等待投递", meta.pending_deliveries, "沿原消息记录执行", "clock"),
      metric("结果待核对", meta.uncertain_deliveries, "尚无可确认的最终结果", "warning"),
      metric("投递记录", meta.total_deliveries, "包含完成、失败与未知", "account")];
  const policy = voice ? meta.reply_policy : null;
  const note = voice
    ? (!meta.enabled ? "当前未启用语音能力" : policy?.enabled ? (policy.mode === "voice_input" ? "优先回应语音输入" : "短回复可使用语音") +
      " · 最多 " + count(policy.max_chars) + " 字 · 最长等待 " + duration(policy.wait_seconds) : "当前未启用语音回复")
    : "平台回执与资料观测分别记录。昵称、签名和头像共用资料修改次数上限。";
  const detail = !voice && Array.isArray(meta.limits) ? el("details", { class: "media-limits panel" },
    el("summary", {}, "当前限频与冷却", el("span", { class: "muted" }, "展开查看各项")),
    el("ul", { class: "limit-grid" }, meta.limits.map(item => el("li", {},
      el("div", { class: "limit-heading" }, el("strong", {}, item.title), pill(item.state)),
      el("p", {}, "每日最多 " + count(item.daily_limit) + " 次 · 间隔 " + duration(item.cooldown_seconds)),
      el("p", { class: "muted" }, item.reason || "暂无状态说明"),
      item.next_after && Date.parse(item.next_after) > Date.parse(state.data.observed_at)
        ? el("p", { class: "muted" }, "冷却截至 " + formatDate(item.next_after)) : null)))) : null;
  return el("section", { class: "media-overview", "aria-label": voice ? "语音概况" : "账号行为概况" },
    el("div", { class: "media-summary" }, el("div", {},
      el("p", {}, pill(meta.enabled ? "ENABLED" : "DISABLED", meta.enabled ? "配置已开启" : "配置未开启"), el("span", {}, note)),
      meta.policy_problem ? el("p", { class: "form-error" }, meta.policy_problem) : null,
      voice ? el("p", { class: "panel-note" }, "节点联系反映最近记录，不代表设备健康检查；显示最近 " + count(meta.displayed_jobs) + " 条任务。") : null),
      link("查看配置", "configuration")), el("div", { class: "metrics" }, metrics), detail);
}
function renderCollection() {
  const filters = state.route === "memories"
    ? [["all", "全部记忆"], ["review", "待确认"]]
    : [["all", "全部记录"], ["attention", "需要留意"], ["completed", "已完成"]];
  const filterBar = el("div", { class: "filters", "aria-label": "记录筛选" }, filters.map(([value, label]) =>
    el("button", { class: "filter-button", type: "button", "aria-pressed": state.filter === value, onclick: () => {
      state.filter = value; state.selection = null;
      if (state.route === "memories") loadRoute({ reset: true });
      else render();
    } }, label)));
  const search = el("input", { id: "record-search", type: "search", value: state.query,
    maxlength: state.route === "memories" ? "100" : "200",
    placeholder: state.route === "memories" ? "搜索记忆…" : "筛选当前记录…", "aria-label": state.route === "memories" ? "搜索记忆" : "筛选当前记录",
    oninput: event => {
      state.query = event.target.value;
      clearTimeout(state.searchTimer);
      if (state.route === "memories") state.searchTimer = setTimeout(() => loadRoute(), 300);
      else renderCollectionBody();
    } });
  const tools = el("div", { class: "view-tools" }, filterBar,
    el("div", { class: "search-field" }, icon("search"), search));
  const headingActions = state.route === "life" ? el("div", { class: "button-row life-actions" },
    action("按日期查看日记", openDiaryLookup, { symbol: "content", small: true }),
    action("管理日记与主动行为", openModeDialog, { symbol: "settings", small: true })) : null;
  const body = el("div", { id: "collection-body" });
  const result = el("div", {}, mediaRoute() ? renderMediaOverview() : null, headingActions, tools, body);
  renderCollectionBody(body);
  return result;
}

function renderCollectionBody(target = $("#collection-body")) {
  if (!target) return;
  const items = filteredItems();
  if (!items.length) {
    const searched = state.query || state.filter !== "all";
    target.replaceChildren(el("section", { class: "panel" }, empty(searched ? "没有匹配的记录" : "还没有记录",
      searched ? "试试其他关键词，或切换回全部记录。" : "实际发生的内容会出现在这里。暂无记录不代表服务离线。",
      { symbol: state.route === "memories" ? "memory" : state.route === "memes" ? "image" : "inbox" })));
    return;
  }
  if (!items.some(item => String(item.id) === state.selection)) state.selection = String(items[0].id);
  const timeline = el("ol", { class: "timeline", "aria-label": "记录列表" }, items.map(item => {
    const selected = String(item.id) === state.selection;
    return el("li", { class: "timeline-item " + (selected ? "selected" : "") }, el("span", { class: "timeline-dot", "aria-hidden": "true" }),
      el("button", { class: "timeline-card", type: "button", "aria-pressed": selected, "aria-controls": "record-detail", onclick: () => {
        state.selection = String(item.id); renderCollectionBody();
        if (matchMedia("(max-width: 920px)").matches) {
          $("#record-detail").scrollIntoView({ block: "start", behavior: "instant" });
          $("#detail-heading").focus({ preventScroll: true });
        }
      } },
      el("span", { class: "record-overline" }, el("span", { class: "record-kind" }, icon(kindIcon(item.kind)), KINDS[item.kind] || item.kind || "记录"),
        el("time", { datetime: item.occurred_at || "" }, formatDate(item.occurred_at))),
      el("span", { class: "record-title", title: item.title }, displayTitle(item)),
      item.summary && !(mediaRoute() && item.summary === item.detail?.state_label) ? el("span", { class: "record-summary" }, item.summary) : null,
      el("span", { class: "record-bottom" }, pill(item.status, itemStatusLabel(item)),
        el("span", { class: "record-ref", title: String(item.id || "") }, mediaRoute() ? "记录 " + String(item.id || "").slice(-8) : String(item.id || "")))));
  }));
  const selected = items.find(item => String(item.id) === state.selection);
  const more = state.route === "activity" && state.data.next_cursor !== null && state.data.next_cursor !== undefined
    ? action("加载更早的记录", event => { event.currentTarget.disabled = true; loadRoute({ more: true }); }, { small: true }) : null;
  if (more) more.id = "load-more";
  target.replaceChildren(el("div", { class: "split-view" }, el("div", {}, timeline, more), renderDetail(selected)));
}

function renderDetail(item) {
  const details = item.detail && typeof item.detail === "object" && !Array.isArray(item.detail) ? item.detail : {};
  const body = el("div", { class: "panel-body" }, pill(item.status, itemStatusLabel(item)), el("h3", { class: "detail-title" }, displayTitle(item)));
  if (state.route === "activity" && ACTIVITY_TITLES[item.title]) {
    body.append(el("div", { class: "detail-section" }, el("h3", {}, "操作代码"), el("p", { class: "detail-text data" }, item.title)));
  }
  if (item.summary && !(mediaRoute() && item.summary === details.state_label)) body.append(el("p", { class: "detail-text" }, item.summary));
  body.append(el("div", { class: "detail-section" }, el("h3", {}, "记录信息"),
    el("div", { class: "key-value" }, el("span", {}, "发生时间"), el("time", {}, formatDate(item.occurred_at))),
    !mediaRoute() ? el("div", { class: "key-value" }, el("span", {}, "记录编号"), el("span", { class: "data" }, item.id)) : null));
  const technical = [];
  for (const [key, value] of Object.entries(details)) {
    if (["can_reconcile", "reconciliation_prepared", "reconciliation_reason", "state_label"].includes(key) || SECRET_KEY.test(key) || value === null || value === undefined) continue;
    if (mediaRoute() && (/_sha256$/.test(key) || ["message_id", "active_job_id", "native_retcode", "resource_id", "last_error_code"].includes(key))) {
      technical.push([key, value]); continue;
    }
    const label = DETAIL_LABELS[key] || key;
    const section = el("div", { class: "detail-section" }, el("h3", {}, label));
    const url = /url|source_page/.test(key) ? safeUrl(value) : null;
    if (mediaRoute() && key === "sources" && Array.isArray(value)) {
      section.append(value.length ? el("ul", { class: "source-list" }, value.map(source => el("li", {},
        safeUrl(source.url) ? el("a", { href: safeUrl(source.url), target: "_blank", rel: "noopener noreferrer" }, source.title, icon("arrow")) : el("span", {}, source.title || "来源当前不可用"),
        pill(source.status), source.coverage ? el("details", { class: "source-coverage" },
          el("summary", {}, source.coverage.scope === "first_part_subtitles" ? "第 " + (source.coverage.part ?? "—") + " 部分字幕记录" : source.coverage.scope === "article_text" ? "文章文字记录" : "查看来源读取范围"),
          el("p", { class: "muted" }, safeValue(source.coverage))) : null))) : el("p", { class: "detail-text" }, "本条计划没有可展示的公开出处。"));
    } else if (mediaRoute() && key === "limits") {
      section.append(el("p", { class: "detail-text" }, "合成：最多 " + count(value.tts_chars) + " 字 / " + duration(value.tts_seconds) + "，输出 " + bytes(value.tts_output_bytes)),
        el("p", { class: "detail-text" }, "转写：输入 " + bytes(value.asr_input_bytes) + " / " + duration(value.asr_seconds) + "，最多 " + count(value.asr_chars) + " 字、" + count(value.asr_segments) + " 段"));
    } else if (url) section.append(el("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "detail-text" }, String(value), icon("arrow")));
    else section.append(el("p", { class: "detail-text" },
      mediaRoute() ? mediaDetailValue(key, value) : key === "action_type" && DELIVERY_ACTIONS[value] ? DELIVERY_ACTIONS[value] : safeValue(value)));
    body.append(section);
  }
  if (mediaRoute()) body.append(el("details", { class: "technical-details" }, el("summary", {}, "来源与核验编号"),
    el("div", { class: "detail-section" }, el("h3", {}, "记录编号"), el("p", { class: "detail-text data" }, item.id)),
    technical.map(([key, value]) => el("div", { class: "detail-section" }, el("h3", {}, DETAIL_LABELS[key] || key), el("p", { class: "detail-text data" }, safeValue(value))))));
  const actions = el("div", { class: "detail-actions" });
  if (state.route === "memories" && !["DELETED", "FORGOTTEN", "REDACTED", "RETRACTED"].includes(item.status)) {
    if (item.status !== "CONFIRMED") actions.append(action("确认记忆", () => memoryAction(item, "confirm"), { style: "subtle", small: true, symbol: "check" }));
    actions.append(action("修订", () => memoryAction(item, "correct"), { small: true, symbol: "edit" }),
      action("忘记", () => memoryAction(item, "forget"), { style: "danger", small: true, symbol: "trash" }));
  }
  const day = diaryDay(item);
  if (state.route === "life" && day) actions.append(action("查看这一天", () => showDiary(day), { small: true, symbol: "content" }));
  if (actions.childElementCount) body.append(actions);
  if (state.route === "diagnostics" && item.kind === "outbox" && item.status === "DELIVERY_UNCERTAIN"
    && Object.hasOwn(DELIVERY_ACTIONS, details.action_type)
    && typeof item.id === "string" && /^msg_[a-f0-9]{32}$/.test(item.id)) {
    body.append(details.can_reconcile === true && details.reconciliation_prepared === true
      ? deliveryReconciliation(item) : el("div", { class: "reconciliation-note", role: "status" },
        el("strong", {}, "需要先准备核对"), el("p", {}, typeof details.reconciliation_reason === "string"
          ? details.reconciliation_reason : "请先停止发送进程并准备核对凭证，完成后刷新此页。")));
  }
  return el("aside", { class: "panel detail", id: "record-detail", "aria-labelledby": "detail-heading" },
    el("div", { class: "panel-header" }, el("h2", { id: "detail-heading", tabindex: "-1" }, "记录详情"),
      el("span", { class: "mini-icon" }, icon(kindIcon(item.kind)))), body);
}

function mediaDetailValue(key, value) {
  if (/_bytes$/.test(key)) return bytes(value);
  if (/_seconds$/.test(key)) return duration(value);
  if (/_at$/.test(key) && typeof value === "string") return formatDate(value);
  if (key === "manifest_state" && value === "UNOBSERVED") return "暂无任务核验证据";
  if (key === "freshness_state" && value === "UNOBSERVED") return "尚无资料观测";
  if (key === "resource_state" && value === "READY") return "上次记录为空闲";
  if (key === "task_state" && value === "READY") return "音频已生成";
  if (key === "plan_state" && value === "READY") return "计划已准备";
  if (key === "receipt_state") return ({ SUCCEEDED: "平台已接受", REJECTED: "平台已拒绝", UNKNOWN: "平台结果不明", UNOBSERVED: "尚无完整平台回执", INVALID: "回执不完整或不匹配" })[value] || STATUS[value] || value;
  if (["task_state", "plan_state", "delivery_state", "receipt_state", "resource_state", "freshness_state", "manifest_state"].includes(key)) return STATUS[value] || value;
  if (key === "capabilities") return Array.isArray(value) ? value.map(item => ({ asr: "语音转写", tts: "语音合成" })[item] || item).join("、") : "未记录";
  if (key === "operation") return value === "asr" ? "语音转写" : value === "tts" ? "语音合成" : value;
  if (key === "field") return ({ nickname: "昵称", signature: "签名", avatar: "头像" })[value] || value;
  if (key === "action_type") return ({ "qzone.publish": "QQ空间发布", "qq.profile.nickname": "昵称更新", "qq.profile.signature": "签名更新", "qq.profile.avatar": "头像更新" })[value] || value;
  return safeValue(value);
}

function openDialog(title, content) {
  $("#dialog-title").textContent = title;
  $("#dialog-content").replaceChildren(content);
  if (!$("#action-dialog").open) $("#action-dialog").showModal();
  requestAnimationFrame(() => $("input, textarea, select", $("#dialog-content"))?.focus());
}
function formError(form, text) {
  let field = $(".form-error", form);
  if (!field) { field = el("p", { class: "form-error", role: "alert" }); form.append(field); }
  field.textContent = text;
  field.hidden = !text;
}
async function perform(form, button, operation, success) {
  const label = button.textContent;
  const epoch = state.epoch;
  const generation = state.generation;
  button.disabled = true;
  button.textContent = "正在处理…";
  clearTimeout(state.toastTimer);
  $("#toast").hidden = true;
  formError(form, "");
  try {
    const result = await operation();
    if (epoch !== state.epoch || generation !== state.generation || !state.session) return;
    await success(result);
  } catch (error) {
    if (epoch === state.epoch && generation === state.generation && state.session) {
      if (form.isConnected) formError(form, errorText(error));
      else notice(errorText(error), "error");
    }
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function deliveryReconciliation(item) {
  const form = el("form", { class: "delivery-reconcile", "aria-label": "核对这条投递" });
  const choices = [["SENT", "标记已送达"], ["CANCELLED", "结束核对"]];
  const buttons = choices.map(([outcome, label]) => action(label, async event => {
    if (state.deliverySaves.has(item.id)) return;
    const submission = { epoch: state.epoch, outcome };
    state.deliverySaves.set(item.id, submission);
    buttons.forEach(button => { button.disabled = true; });
    form.setAttribute("aria-busy", "true");
    try {
      await perform(form, event.currentTarget,
        () => api.write("/deliveries/" + encodeURIComponent(item.id) + "/reconcile", { outcome }, "POST",
          result => result.state === outcome), async result => {
          const current = state.data?.items?.find(record => String(record.id) === item.id);
          if (current) {
            current.status = result.state;
            current.detail = { ...current.detail, can_reconcile: false };
          }
          renderCollectionBody();
          toast(outcome === "SENT" ? "已记录为已送达。" : "核对已结束。");
          await loadRoute();
        });
    } finally {
      if (state.deliverySaves.get(item.id) === submission) state.deliverySaves.delete(item.id);
      if (form.isConnected) {
        buttons.forEach(button => { button.disabled = false; });
        form.setAttribute("aria-busy", "false");
      } else if (submission.epoch === state.epoch && state.session && state.route === "diagnostics") {
        renderCollectionBody();
      }
    }
  }, { style: outcome === "SENT" ? "primary" : "" }));
  const running = state.deliverySaves.get(item.id);
  if (running) {
    buttons.forEach((button, index) => {
      button.disabled = true;
      if (choices[index][0] === running.outcome) button.textContent = "正在处理…";
    });
  }
  form.setAttribute("aria-busy", String(Boolean(running)));
  form.addEventListener("submit", event => event.preventDefault());
  form.append(el("p", { class: "panel-note" }, "记录核对结果，不会再次发送。"),
    el("div", { class: "button-row" }, buttons));
  return form;
}

function memoryAction(item, decision) {
  const titles = { confirm: "确认这条记忆", correct: "修订记忆", forget: "忘记这条记忆" };
  const form = el("form");
  form.append(el("p", {}, decision === "forget" ? "忘记后，这条记忆及其相关引用将按遗忘流程处理。请核对要移除的内容。" : "修改会留下操作记录，并沿用原有来源核验。"),
    el("div", { class: "detail-text" }, item.detail?.content || item.summary || item.title));
  let input;
  if (decision === "correct") {
    input = el("textarea", { id: "memory-correction", required: true, rows: "6", value: item.detail?.content || item.summary || item.title || "" });
    form.append(el("label", { for: "memory-correction" }, "修订后的内容"), input);
  }
  const submit = el("button", { type: "submit", class: "button " + (decision === "forget" ? "danger" : "primary") },
    decision === "forget" ? "确认忘记" : decision === "correct" ? "保存修订" : "确认记忆");
  form.append(el("div", { class: "dialog-actions" }, action("取消", () => $("#action-dialog").close()), submit));
  form.addEventListener("submit", event => {
    event.preventDefault();
    const body = { decision, ...(input ? { corrected_content: input.value.trim() } : {}) };
    if (input && !body.corrected_content) { formError(form, "请填写修订后的内容。"); return; }
    perform(form, submit, () => api.write("/memories/" + encodeURIComponent(item.id) + "/decision", body, "POST",
      result => typeof result.memory_id === "string" && typeof result.state === "string" && Boolean(result.state)
        && (String(result.memory_id) === String(item.id) || decision === "correct" && String(result.supersedes) === String(item.id))), async result => {
      $("#action-dialog").close();
      state.selection = String(result.memory_id);
      toast("操作已记录：" + (STATUS[result.state] || result.state));
      await loadRoute();
    });
  });
  openDialog(titles[decision], form);
}

function diaryDay(item) {
  const value = item.detail?.local_date || item.detail?.day || (item.kind === "diary" ? item.id : null);
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}
function openDiaryLookup() {
  const input = el("input", { id: "diary-date", type: "date", required: true });
  const form = el("form", {}, el("label", { for: "diary-date" }, "日记日期"), input);
  form.append(el("div", { class: "dialog-actions" }, action("取消", () => $("#action-dialog").close()),
    el("button", { type: "submit", class: "button primary" }, "查看日记")));
  form.addEventListener("submit", event => { event.preventDefault(); showDiary(input.value); });
  openDialog("按日期查看日记", form);
}
async function showDiary(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
  const epoch = state.epoch;
  openDialog(day + " 的日记", el("p", { role: "status" }, "正在读取日记…"));
  try {
    const result = await api.request("/diary/" + day);
    if (epoch !== state.epoch || !state.session) return;
    if (result.local_date !== day || typeof result.entry_id !== "string" || typeof result.body_md !== "string") {
      throw new ApiError("CONSOLE_RESPONSE_INVALID");
    }
    const item = result;
    const text = item.body_md;
    const content = el("div", {}, el("h3", { class: "detail-title" }, item.title || day + " 的日记"),
      el("div", { class: "detail-text" }, text || "这一天还没有可显示的日记正文。"));
    content.append(el("div", { class: "dialog-actions" }, action("修订", () => diaryAction(day, "correction", text), { symbol: "edit" }),
      action("忘记这一天", () => diaryAction(day, "forget", ""), { style: "danger", symbol: "trash" })));
    openDialog(day + " 的日记", content);
  } catch (error) {
    if (epoch === state.epoch && state.session) openDialog(day + " 的日记", error.code === "DIARY_NOT_FOUND"
      ? empty("这一天还没有日记", "可以选择其他日期查看已有记录。", { symbol: "content" })
      : el("p", { class: "form-error", role: "alert" }, errorText(error)));
  }
}
function diaryAction(day, operation, current) {
  const form = el("form", {}, el("p", {}, operation === "forget"
    ? "这一天的日记会进入遗忘流程，请确认日期：" + day : "修订后的正文会标记为主人修订。"));
  const input = operation === "correction" ? el("textarea", { id: "diary-correction", required: true, rows: "8", value: current }) : null;
  if (input) form.append(el("label", { for: "diary-correction" }, "日记正文"), input);
  const submit = el("button", { type: "submit", class: "button " + (operation === "forget" ? "danger" : "primary") }, operation === "forget" ? "确认忘记" : "保存修订");
  form.append(el("div", { class: "dialog-actions" }, action("取消", () => $("#action-dialog").close()), submit));
  form.addEventListener("submit", event => {
    event.preventDefault();
    const body = input ? { text: input.value.trim() } : {};
    if (input && !body.text) { formError(form, "请填写修订后的日记正文。"); return; }
    perform(form, submit, () => api.write("/diary/" + day + "/" + operation, body, "POST",
      result => typeof result.state === "string" && Boolean(result.state) || typeof result.entry_id === "string" && Number.isInteger(result.version)), async () => {
      $("#action-dialog").close(); toast("日记操作已记录。"); await loadRoute();
    });
  });
  openDialog(operation === "forget" ? "忘记日记" : "修订日记", form);
}
function openModeDialog() {
  const target = el("select", { id: "mode-target", required: true },
    [["diary", "日记"], ["life", "生活与成长"], ["proactive", "主动交流"]].map(([value, label]) => el("option", { value }, label)));
  const enabled = el("select", { id: "mode-enabled", required: true }, el("option", { value: "" }, "请选择操作"),
    el("option", { value: "true" }, "启用"), el("option", { value: "false" }, "暂停"));
  const form = el("form", {}, el("p", {}, "只修改所选能力。启用后的实际任务仍按当前授权与额度执行。"),
    el("label", { for: "mode-target" }, "要管理的能力"), target, el("label", { for: "mode-enabled" }, "操作"), enabled);
  const submit = el("button", { type: "submit", class: "button primary" }, "保存开关");
  form.append(el("div", { class: "dialog-actions" }, action("取消", () => $("#action-dialog").close()), submit));
  form.addEventListener("submit", event => {
    event.preventDefault();
    perform(form, submit, () => api.write("/mode", { target: target.value, enabled: enabled.value === "true" }, "POST",
      result => typeof result.enabled === "boolean" || result.state === "COMPLETE"), async () => {
      $("#action-dialog").close(); toast("开关操作已记录，请以最新状态为准。"); await loadRoute();
    });
  });
  openDialog("管理日记与主动行为", form);
}

async function saveSettings(form, button, kind, changes, version) {
  const budget = kind === "budget";
  const slot = budget ? "budgetSave" : "configSave";
  if (state[slot]?.generation === state.generation) return;
  const draft = budget ? state.budgetDraft : state.configDraft;
  const submission = { generation: state.generation,
    draft: new Map(Object.keys(changes).map(key => [key, draft.get(key)])) };
  state[slot] = submission;
  try {
    await perform(form, button, () => api.write(budget ? "/cloud-budget" : "/configuration",
      { expected_version: version, changes }, "PUT", result => Number.isInteger(result.version)
        && (budget ? result.config && typeof result.config === "object"
          : Array.isArray(result.fields) && Number.isInteger(result.active_version))), result => {
      for (const [key, raw] of submission.draft) {
        if (draft.has(key) && draft.get(key) === raw) draft.delete(key);
      }
      if (budget) state.data.cloud_budget = result;
      else {
        const currentBudget = state.data.cloud_budget;
        state.data = result;
        if (currentBudget && (!result.cloud_budget || currentBudget.version > result.cloud_budget.version)) {
          state.data.cloud_budget = currentBudget;
        }
      }
      state.dirty = state.configDraft.size > 0 || state.budgetDraft.size > 0;
      render();
      toast(budget ? "预算已保存，后续预留使用新版本。已发生费用与在途任务保持原记录。"
        : "配置已保存，等待重启与运行窗口交接。");
    });
  } finally {
    if (state[slot] === submission) state[slot] = null;
    if (submission.generation === state.generation && state.route === "configuration" && state.session) {
      const currentButton = $(budget ? "#budget-form button[type=submit]" : "#configuration-form button[type=submit]");
      if (currentButton) {
        currentButton.disabled = false;
        currentButton.textContent = budget ? "保存预算" : "保存配置";
      }
    }
  }
}

function renderConfiguration(data) {
  if (!Array.isArray(data.fields) || !Number.isInteger(data.version) || !Number.isInteger(data.active_version)) {
    return el("section", { class: "panel" }, empty("配置数据不完整", "请刷新核对，当前无法安全编辑配置。", { symbol: "warning", error: true, retry: true }));
  }
  const fields = data.fields.filter(field => typeof field.key === "string" && typeof field.label === "string");
  const groups = [...new Set(fields.map(field => field.group || "system"))];
  const hasBudget = Number.isInteger(data.cloud_budget?.version) && data.cloud_budget?.config && typeof data.cloud_budget.config === "object";
  if (hasBudget) groups.push("budget");
  if (!groups.length) return el("section", { class: "panel" }, empty("暂无可编辑配置", "服务提供配置字段后，会在这里按类别显示。", { symbol: "settings" }));
  if (!groups.includes(state.group)) state.group = groups[0];
  const navigation = el("nav", { class: "config-navigation", "aria-label": "配置分类" }, groups.map(group =>
    el("button", { type: "button", "aria-pressed": state.group === group, onclick: () => {
      state.group = group; render();
    } }, GROUPS[group] || group)));
  if (state.group === "budget") return el("div", { class: "config-layout" }, navigation, renderCloudBudget(data.cloud_budget));
  const form = el("form", { class: "config-form", id: "configuration-form" });
  for (const field of fields.filter(item => (item.group || "system") === state.group)) form.append(configField(field));
  const saving = state.configSave?.generation === state.generation;
  const submit = el("button", { type: "submit", class: "button primary", disabled: saving }, saving ? "正在处理…" : "保存配置");
  form.append(el("div", { class: "config-actions" },
    el("p", { class: "field-hint" }, "保存后，等待重启与运行窗口交接。"), submit));
  form.addEventListener("submit", event => {
    event.preventDefault();
    const changes = Object.create(null);
    try {
      for (const [key, raw] of state.configDraft) {
        const field = fields.find(item => item.key === key);
        if (!field) continue;
        const parsed = parseField(field, raw);
        if (field.type === "secret" && parsed === "") continue;
        if (field.type === "secret" || JSON.stringify(parsed) !== JSON.stringify(field.value)) changes[key] = parsed;
      }
    } catch (error) { formError(form, error.message); return; }
    if (!Object.keys(changes).length) { formError(form, "没有需要保存的修改。"); return; }
    saveSettings(form, submit, "configuration", changes, data.version);
  });
  const status = el("div", { class: "notice " + (data.pending_restart ? "" : "info") }, icon(data.pending_restart ? "clock" : "check"),
    el("p", {}, data.pending_restart ? "已保存的配置尚待重启与运行窗口交接。当前运行仍使用生效版本。" : "当前保存版本与生效版本一致。"));
  const header = el("div", { class: "panel-header" }, el("h2", {}, GROUPS[state.group] || state.group),
    el("span", { class: "status-pill" }, "版本 " + data.version));
  const history = Array.isArray(data.history) ? data.history : [];
  const historyBody = history.length ? el("ul", { class: "mini-list" }, history.slice(0, 8).map(entry =>
    el("li", {}, el("div", { class: "mini-row" }, el("span", { class: "mini-icon" }, icon("settings")),
      el("div", { class: "mini-row-main" }, el("p", { class: "mini-row-title" }, "配置版本 " + entry.version),
        el("p", { class: "mini-row-meta" }, el("time", {}, formatDate(entry.created_at)),
          el("span", {}, Array.isArray(entry.fields) ? entry.fields.map(key => fields.find(field => field.key === key)?.label || key).join("、") : "")))))))
    : empty("还没有修改记录", "通过控制台保存配置后，版本与修改字段会记录在这里。", { symbol: "settings" });
  return el("div", {}, status, el("div", { class: "config-layout" }, navigation,
    el("div", { class: "stack" }, el("section", { class: "panel" }, header, form,
      el("div", { class: "config-meta" }, "当前生效：版本 " + data.active_version + " · 已保存：版本 " + data.version)),
    panel("修改记录", historyBody))));
}

function renderCloudBudget(policy) {
  const fields = [
    { key: "daily_micro_cny", label: "每日云端总限额（元）", scale: 1e6, min: 0, max: 1e12 },
    { key: "video_per_item_micro_cny", label: "每个视频任务的累计限额（元）", scale: 1e6, min: 0, max: 1e12 },
    { key: "asr_per_item_micro_cny", label: "每条语音识别限额（元）", scale: 1e6, min: 0, max: 1e12 },
    { key: "usd_to_cny_micro", label: "每 1 美元折合人民币（元）", scale: 1e6, min: 1, max: 100000000 },
    { key: "fx_source", label: "汇率依据", scale: 1 },
    { key: "fx_validity_seconds", label: "汇率有效期（天）", scale: 86400, min: 60, max: 366 * 86400 },
  ];
  const form = el("form", { class: "config-form", id: "budget-form" });
  for (const field of fields) {
    const current = policy.config[field.key];
    const value = state.budgetDraft.has(field.key) ? state.budgetDraft.get(field.key)
      : typeof current === "number" ? String(current / field.scale) : current ?? "";
    const input = el("input", { id: "budget-" + field.key, type: field.key === "fx_source" ? "text" : "number",
      value, required: true, step: field.scale === 1e6 ? "0.000001" : "any",
      min: field.min === undefined ? undefined : field.min / field.scale,
      max: field.max === undefined ? undefined : field.max / field.scale,
      maxlength: field.key === "fx_source" ? "256" : undefined });
    input.addEventListener("input", () => { state.budgetDraft.set(field.key, input.value); state.dirty = true; });
    form.append(el("div", { class: "config-field" }, el("label", { for: input.id }, field.label), input));
  }
  const saving = state.budgetSave?.generation === state.generation;
  const submit = el("button", { type: "submit", class: "button primary", disabled: saving }, saving ? "正在处理…" : "保存预算");
  form.append(el("div", { class: "config-actions" }, el("p", { class: "field-hint" }, "独立保存，用于后续费用预留。"), submit));
  form.addEventListener("submit", event => {
    event.preventDefault();
    const changes = Object.create(null);
    try {
      for (const [key, raw] of state.budgetDraft) {
        const field = fields.find(item => item.key === key);
        if (!field) continue;
        let value;
        if (key === "fx_source") {
          value = raw.trim();
          if (!value || value.length > 256) throw new Error("请填写有效的汇率依据。");
        } else if (field.scale === 1e6) {
          const match = raw.trim().match(/^(\d+)(?:\.(\d{0,6}))?$/);
          if (!match) throw new Error(field.label + "：最多支持 6 位小数。");
          value = Number(match[1]) * 1e6 + Number((match[2] || "").padEnd(6, "0"));
        } else {
          const scaled = Number(raw) * field.scale;
          value = Math.round(scaled);
          if (raw === "" || !Number.isFinite(scaled) || Math.abs(scaled - value) > 0.00001) throw new Error(field.label + "：请填写能换算为整数秒的时长。");
        }
        if (typeof value === "number" && (!Number.isSafeInteger(value) || value < field.min || value > field.max)) throw new Error(field.label + "：数值超出允许范围。");
        if (value !== policy.config[key]) changes[key] = value;
      }
    } catch (error) { formError(form, error.message); return; }
    if (!Object.keys(changes).length) { formError(form, "没有需要保存的预算修改。"); return; }
    saveSettings(form, submit, "budget", changes, policy.version);
  });
  return el("div", { class: "stack" },
    el("div", { class: "notice info" }, icon("wallet"), el("p", {}, "预算独立生效于后续预留。调整限额或汇率不会重置已用额度，也不会重算在途任务。")),
    panel("云端费用与预留", form, el("span", { class: "status-pill active" }, "预算版本 " + policy.version)),
    panel("当前预算记录", el("div", { class: "panel-body" },
      el("div", { class: "key-value" }, el("span", {}, "生效时间"), el("span", {}, formatDate(policy.effective_at))),
      el("div", { class: "key-value" }, el("span", {}, "汇率有效期至"), el("span", {}, formatDate(policy.fx_valid_until))),
      el("div", { class: "key-value" }, el("span", {}, "记账时区"), el("span", {}, policy.timezone || "未记录")),
      el("p", { class: "panel-note" }, "金额依据配置费率和汇率计算，不代表服务商费用上限。"))));
}

function configField(field) {
  const id = "config-" + encodeURIComponent(field.key);
  const type = field.type;
  const draftValue = state.configDraft.get(field.key);
  const value = state.configDraft.has(field.key) ? (draftValue === null && ["object", "array"].includes(type) ? "null" : draftValue)
    : type === "secret" ? "" : ["object", "array"].includes(type) ? JSON.stringify(field.value, null, 2) : field.value ?? "";
  let input;
  if (type === "boolean") input = el("input", { type: "checkbox", id, checked: Boolean(value) });
  else if (Array.isArray(field.schema?.enum)) input = el("select", { id }, field.schema.enum.map(option => el("option", { value: JSON.stringify(option) }, String(option))));
  else if (["object", "array"].includes(type)) input = el("textarea", { id, rows: "5", spellcheck: "false", value });
  else input = el("input", { id, type: type === "secret" ? "password" : ["integer", "number"].includes(type) ? "number" : "text",
    value, autocomplete: type === "secret" ? "new-password" : "off", step: type === "integer" ? "1" : type === "number" ? "any" : undefined,
    min: field.minimum, max: field.maximum, placeholder: type === "secret" ? (field.configured ? "已配置 · 留空保留现有值" : "尚未配置") : field.nullable ? "未配置" : undefined });
  if (input.tagName === "SELECT") input.value = JSON.stringify(state.configDraft.has(field.key) ? JSON.parse(value) : field.value);
  input.dataset.setting = field.key;
  const nullStatus = el("span", { class: "field-hint" });
  function updateDraft(raw) {
    let unchanged = type === "secret" && raw === "";
    if (type !== "secret") {
      try { unchanged = JSON.stringify(parseField(field, raw)) === JSON.stringify(field.value); }
      catch { unchanged = false; }
    }
    const submitted = state.configSave?.generation === state.generation && state.configSave.draft.has(field.key);
    if (unchanged && (!submitted || type === "secret")) state.configDraft.delete(field.key);
    else state.configDraft.set(field.key, raw);
    state.dirty = state.configDraft.size > 0 || state.budgetDraft.size > 0;
    try { nullStatus.textContent = parseField(field, raw) === null ? "未配置" : ""; }
    catch { nullStatus.textContent = ""; }
  }
  input.addEventListener("input", () => updateDraft(type === "boolean" ? input.checked : input.value));
  const row = el("div", { class: "config-field" });
  const label = el("label", { for: id }, field.label, type === "boolean" ? input : el("span", { class: "status-pill" }, "重启生效"));
  row.append(label);
  if (type !== "boolean") row.append(input);
  if (field.description) row.append(el("p", { class: "field-hint" }, field.description));
  if (field.nullable && ["object", "array"].includes(type)) row.append(el("p", { class: "field-hint" }, "填写 JSON；使用 null 清空这项配置。"));
  if (field.nullable === true && type !== "secret") {
    const clear = action("设为未配置", () => {
      updateDraft(null);
      if (type === "boolean") input.checked = false;
      else input.value = ["object", "array"].includes(type) || input.tagName === "SELECT" ? "null" : "";
      input.focus();
    }, { small: true, style: "subtle" });
    clear.setAttribute("aria-label", field.label + "：设为未配置");
    const initial = state.configDraft.has(field.key) ? draftValue : field.value;
    nullStatus.textContent = initial === null ? "未配置" : "";
    row.append(el("div", { class: "button-row" }, clear, nullStatus));
  }
  if (type === "secret") row.append(el("p", { class: "field-hint" }, field.configured ? "已有密钥仅显示配置状态，输入新值后替换。" : "输入密钥后保存；密钥不会回显。"));
  else if (field.active_value !== undefined && JSON.stringify(field.value) !== JSON.stringify(field.active_value)) {
    row.append(el("p", { class: "field-hint" }, "当前生效值：" + safeValue(field.active_value)));
  }
  return row;
}
function parseField(field, raw) {
  if (raw === null && field.nullable === true) return null;
  if (field.type === "boolean") return Boolean(raw);
  if (Array.isArray(field.schema?.enum)) return JSON.parse(raw);
  if (["object", "array"].includes(field.type)) {
    let value;
    try { value = JSON.parse(raw); } catch { throw new Error(field.label + "：请填写有效的 JSON。"); }
    if (value === null && field.nullable === true) return null;
    if (!value || typeof value !== "object" || (field.type === "array") !== Array.isArray(value)) throw new Error(field.label + "：填写类型不匹配。");
    return value;
  }
  if (["integer", "number"].includes(field.type)) {
    const value = raw === "" ? NaN : Number(raw);
    if (!Number.isFinite(value) || field.type === "integer" && !Number.isInteger(value)
      || field.minimum !== undefined && value < field.minimum || field.maximum !== undefined && value > field.maximum) {
      throw new Error(field.label + "：请填写允许范围内的" + (field.type === "integer" ? "整数。" : "数值。"));
    }
    return value;
  }
  return String(raw);
}

for (const target of $$("[data-icon]")) target.replaceChildren(icon(target.dataset.icon));
$("#page-date").textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date());
$("#nav-toggle").addEventListener("click", () => setMenu(true));
$("#nav-close").addEventListener("click", () => { setMenu(false); $("#nav-toggle").focus(); });
$("#nav-scrim").addEventListener("click", () => { setMenu(false); $("#nav-toggle").focus(); });
$("#refresh").addEventListener("click", () => loadRoute());
$("#dialog-close").addEventListener("click", () => $("#action-dialog").close());
$(".skip-link").addEventListener("click", event => {
  event.preventDefault();
  $(state.session ? "#main" : "#password").focus();
});
window.addEventListener("hashchange", navigate);
window.addEventListener("resize", () => { if (!matchMedia("(max-width: 700px)").matches) setMenu(false); });
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && $("#sidebar").classList.contains("open")) {
    setMenu(false); $("#nav-toggle").focus();
  }
  if (event.key === "Tab" && $("#sidebar").classList.contains("open")) {
    const focusable = $$("a, button:not(:disabled)", $("#sidebar")).filter(item => item.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
window.addEventListener("offline", () => {
  if (!state.session) return;
  setConnection("offline", "网络已断开");
  notice("当前网络已断开，页面保留上次读取的记录。恢复连接后可刷新核对。", "", true);
});
window.addEventListener("online", () => { if (state.session && !state.dirty) loadRoute(); });
window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $(".login-submit");
  button.disabled = true;
  $("#login-error").hidden = true;
  try {
    const value = await api.request("/session", { method: "POST", body: { password: $("#password").value }, authenticate: false,
      validate: result => typeof result.csrf_token === "string" && Boolean(result.csrf_token) && Number.isFinite(Date.parse(result.expires_at)) });
    await acceptSession(value);
  } catch (error) {
    $("#login-error").textContent = errorText(error);
    $("#login-error").hidden = false;
  } finally { button.disabled = false; }
});
$("#logout").addEventListener("click", async () => {
  let failure = "";
  try { await api.write("/session", {}, "DELETE"); }
  catch { failure = "本地显示已清除，服务器退出结果未确认。请重新登录后核对。"; }
  clearSession(failure);
  $("#password").focus();
});

async function start() {
  try { await acceptSession(await api.request("/session", { authenticate: false })); }
  catch (error) { clearSession(error.status === 401 ? "" : "暂时无法恢复会话。" + errorText(error)); }
}
start();
