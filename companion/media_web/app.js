"use strict";
const el = (id) => document.getElementById(id);
let session = null, stream = null, recorder = null, playback = null;
let generation = 0, stopping = false;
let timers = new Set(), requests = new Set();
const later = (fn, ms) => { const timer = setTimeout(() => { timers.delete(timer); fn(); }, ms); timers.add(timer); };
const status = (text) => { el("status").textContent = text; };
const errors = {
  MEDIA_DISABLED: "媒体功能尚未启用", MEDIA_COST_BUDGET_EXHAUSTED: "本次费用预算已用完，采集已关闭",
  MEDIA_TRAFFIC_BUDGET_EXHAUSTED: "媒体流量预算已用完，采集已关闭", MEDIA_SESSION_CLOSED: "会话已结束",
  MEDIA_PROVIDER_NOT_CONFIGURED: "媒体服务尚未配置", CONSENT_REQUIRED: "本次授权已失效，采集已关闭",
  PRIVACY_CHANGED: "隐私设置已变更，请重新授权", MEDIA_PROVIDER_UNCERTAIN: "媒体服务未确认结果，采集已关闭",
  CAPABILITY_DISABLED: "所选设备能力尚未启用", OBSERVATION_DISABLED: "观察功能尚未启用",
  MEDIA_SESSION_LIMIT: "当前会话已达上限，请先关闭其他会话"
};
function bytes64(bytes) { let str = ""; for (const byte of bytes) str += String.fromCharCode(byte); return btoa(str); }
async function api(path, body, ownerToken) {
  const controller = new AbortController(); requests.add(controller);
  const deadline = setTimeout(() => controller.abort(), path.endsWith("/chunks") ? 65000 : 5000);
  try {
    const response = await fetch("/internal/v2/media" + path, {
      method: body === undefined ? "GET" : "POST", cache: "no-store", signal: controller.signal,
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + (ownerToken || session?.session_token || "") },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(response.status === 401 ? "授权失败，请检查本次口令" : errors[result.error] || "媒体处理失败，采集已关闭");
    return result;
  } finally { clearTimeout(deadline); requests.delete(controller); }
}
async function stop(reason = "会话已关闭") {
  const old = session; session = null; generation++; stopping = true;
  for (const timer of timers) clearTimeout(timer); timers.clear();
  for (const controller of requests) controller.abort(); requests.clear();
  // Hardware tracks are stopped before any network request; the last blob is discarded.
  stream?.getTracks().forEach((track) => track.stop());
  if (recorder?.state === "recording") recorder.stop();
  recorder = null; stream = null; el("preview").srcObject = null; el("preview").hidden = true;
  if (playback) { playback.pause(); playback.src = ""; playback = null; }
  el("start").disabled = false; el("stop").disabled = true; el("speak").disabled = true; status(reason);
  if (old) {
    const closeDeadline = new AbortController(); const closeTimer = setTimeout(() => closeDeadline.abort(), 5000);
    try { await fetch(`/internal/v2/media/sessions/${old.session_id}/close`, {
      method: "POST", keepalive: true, signal: closeDeadline.signal, headers: { Authorization: "Bearer " + old.session_token }
    }); } catch { /* Local devices are already stopped; server expiry still applies. */ }
    finally { clearTimeout(closeTimer); }
  }
  stopping = false;
}
async function sendChunk(operation, blob, gen) {
  if (!session || gen !== generation) return;
  if (blob.size > session.max_chunk_bytes) throw new Error("单块媒体超过预算，已关闭");
  const data = bytes64(new Uint8Array(await blob.arrayBuffer()));
  if (!session || gen !== generation) return;
  const result = await api(`/sessions/${session.session_id}/chunks`, {
    request_key: crypto.randomUUID(), operation, mime_type: blob.type.split(";")[0], data_base64: data
  });
  if (!session || gen !== generation) return;
  const li = document.createElement("li"); li.textContent = result.text; el("observations").append(li);
  while (el("observations").children.length > 30) el("observations").firstChild.remove();
}
async function recordAudio(gen) {
  if (!session || gen !== generation) return;
  try {
    const mime = ["audio/webm", "audio/ogg", "audio/wav"].find((x) => MediaRecorder.isTypeSupported(x));
    if (!mime) throw new Error("浏览器不支持本次录音格式");
    const chunks = []; const audio = new MediaStream(stream.getAudioTracks());
    const current = new MediaRecorder(audio, { mimeType: mime, audioBitsPerSecond: 24000 });
    recorder = current;
    current.ondataavailable = (event) => { if (session && gen === generation) chunks.push(event.data); };
    current.onerror = () => stop("录音失败，已关闭");
    current.onstop = async () => {
      if (!session || gen !== generation) return;
      try { await sendChunk("audio", new Blob(chunks, { type: mime }), gen); later(() => recordAudio(gen), 100); }
      catch (error) { if (gen === generation) stop(error.message); }
    };
    current.start(); later(() => { if (current.state === "recording") current.stop(); }, 3000);
  } catch (error) { if (gen === generation) stop(error.message); }
}
async function cameraFrame(gen) {
  if (!session || gen !== generation) return;
  try {
    const video = el("preview");
    if (video.videoWidth) {
      const canvas = document.createElement("canvas"); canvas.width = 320;
      canvas.height = Math.round(320 * video.videoHeight / video.videoWidth);
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .65));
      canvas.width = 0; canvas.height = 0;
      if (blob) await sendChunk("camera", blob, gen);
    }
    later(() => cameraFrame(gen), 5000);
  } catch (error) { if (gen === generation) stop(error.message); }
}
async function heartbeat(gen) {
  if (!session || gen !== generation) return;
  try {
    const current = await api(`/sessions/${session.session_id}`);
    if (!session || gen !== generation) return;
    status(`采集中 · ${Math.ceil(current.bytes_reserved / 1000)} / ${Math.ceil(current.max_bytes / 1000)} KB · 原始媒体不留存`);
    later(() => heartbeat(gen), 1000);
  } catch (error) { if (gen === generation) stop(error.message); }
}
el("start-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (session || stopping) return;
  const voice = el("voice").checked, camera = el("camera").checked;
  if (!voice && !camera) { status("请先选择本次允许使用的设备"); return; }
  if (!window.isSecureContext || !navigator.mediaDevices) { status("请通过 HTTPS 或 localhost 打开此页面"); return; }
  const owner = el("owner-token").value; el("owner-token").value = "";
  el("start").disabled = true; const gen = ++generation;
  try {
    session = await api("/sessions", { device_id: "browser-" + crypto.randomUUID(),
      modalities: [voice ? "voice" : null, camera ? "camera" : null].filter(Boolean),
      duration_seconds: Number(el("duration").value), max_bytes: Number(el("budget").value) * 1000 }, owner);
    el("stop").disabled = false;
    later(() => stop("会话已到期"), session.remaining_seconds * 1000); heartbeat(gen);
    const captured = await navigator.mediaDevices.getUserMedia({ audio: voice, video: camera ? { width: 320, height: 240 } : false });
    if (!session || gen !== generation) { captured.getTracks().forEach((track) => track.stop()); return; }
    stream = captured;
    await api(`/sessions/${session.session_id}`);
    if (!session || gen !== generation) return;
    stream.getTracks().forEach((track) => track.addEventListener("ended", () => { if (gen === generation) stop("设备连接已结束"); }));
    if (camera) { el("preview").hidden = false; el("preview").srcObject = stream; cameraFrame(gen); }
    el("speak").disabled = !voice; if (voice) recordAudio(gen);
  } catch (error) { if (gen === generation) stop(error.message); }
});
el("stop").addEventListener("click", () => stop());
document.addEventListener("visibilitychange", () => { if (document.hidden) stop("页面进入后台，已关闭"); });
window.addEventListener("pagehide", () => stop()); window.addEventListener("offline", () => stop("网络已断开，已关闭"));
el("speech-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!session) return; const gen = generation;
  try {
    el("speak").disabled = true;
    if (playback) { playback.pause(); playback.src = ""; playback = null; }
    const result = await api(`/sessions/${session.session_id}/chunks`, { request_key: crypto.randomUUID(),
      operation: "speech", mime_type: "text/plain", text: el("speech").value });
    if (!session || gen !== generation) return;
    const bytes = Uint8Array.from(atob(result.audio_base64), (char) => char.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: result.mime_type }));
    const audio = new Audio(url); playback = audio;
    audio.addEventListener("ended", () => { URL.revokeObjectURL(url); audio.src = ""; if (playback === audio) playback = null; }, { once: true });
    audio.addEventListener("emptied", () => URL.revokeObjectURL(url), { once: true });
    await audio.play();
  } catch (error) { if (gen === generation) stop(error.message); }
  finally { if (session && gen === generation) el("speak").disabled = false; }
});
