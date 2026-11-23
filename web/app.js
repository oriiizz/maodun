import { createSubmitGate, finishUtterance, interpretRecognition } from "./asr_flow.js?v=5";
import { createPresenceStage } from "./stage.js?v=3";
import { createRecorder } from "./speech.js?v=4";

const stage = createPresenceStage(document.getElementById("stage"));
const statusLine = document.getElementById("status-line");
const voiceCaption = document.getElementById("voice-caption");
const micButton = document.getElementById("mic");
const log = document.getElementById("log");
const jumpLatest = document.getElementById("jump-latest");
const questionInput = document.getElementById("question");
const recorder = createRecorder();

const session = {
  mode: "idle",
  phase: "",
  turnId: "",
  generation: 0,
  sessionId: null,
  listening: false,
  controller: null,
  audio: null,
  clips: [],
  clipUrls: [],
  playbackContext: null,
  recognition: null,
  acceptedText: "",
  asrReady: false,
  asrMessage: "",
  ttsReady: false,
};

let pinToLatest = true;
let composing = false;
let composeEndedAt = 0;

stage.render({ mode: "idle" });
loadModuleStatus();

micButton.addEventListener("click", () => {
  if (session.listening) stopAndSend();
  else beginListening();
});

const VISITOR_KEY = "maodun.visitor";
const SESSION_KEY = "maodun.session";
const restored = restoreConversation();

function visitorId() {
  const saved = localStorage.getItem(VISITOR_KEY) || "";
  if (/^vis-[a-f0-9]{24}$/.test(saved)) return saved;
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const created = `vis-${[...bytes].map((item) => item.toString(16).padStart(2, "0")).join("")}`;
  localStorage.setItem(VISITOR_KEY, created);
  return created;
}

function apiHeaders(extra = {}) {
  return { "X-Visitor-Id": visitorId(), ...extra };
}

document.getElementById("history-toggle").addEventListener("click", showHistory);
document.getElementById("download-chat").addEventListener("click", downloadCurrent);
document.getElementById("delete-chat").addEventListener("click", deleteCurrent);
document.getElementById("delete-all").addEventListener("click", deleteAllHistory);
document.getElementById("cancel").addEventListener("click", () => cancelCurrent("已停止"));
document.getElementById("new-session").addEventListener("click", startNewSession);
document.getElementById("text-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;
  questionInput.value = "";
  fitComposer();
  askFromText(question);
});
questionInput.addEventListener("input", fitComposer);
questionInput.addEventListener("compositionstart", () => { composing = true; });
questionInput.addEventListener("compositionend", () => {
  composing = false;
  composeEndedAt = Date.now();
});
questionInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  if (event.isComposing || composing || event.keyCode === 229) return;
  if (Date.now() - composeEndedAt < 40) return;
  event.preventDefault();
  event.currentTarget.form?.requestSubmit();
});
log.addEventListener("scroll", () => {
  const distance = log.scrollHeight - log.scrollTop - log.clientHeight;
  pinToLatest = distance < 72;
  jumpLatest.hidden = pinToLatest || log.scrollHeight <= log.clientHeight + 8;
});
jumpLatest.addEventListener("click", () => {
  pinToLatest = true;
  followLatest();
});

async function beginListening() {
  await restored;
  cancelCurrent("", false);
  pinToLatest = true;
  const generation = nextGeneration();
  if (!session.asrReady) {
    fail(generation, session.asrMessage || "百度实时语音识别尚未配置。");
    return;
  }
  micButton.disabled = true;
  setStatus("正在打开麦克风");
  clearDraft();
  session.acceptedText = "";
  const pendingAudio = [];
  let recognition = null;
  try {
    await recorder.start((samples, level) => {
      if (generation !== session.generation || session.mode !== "listening") return;
      stage.render({ mode: "listening", samples, inputLevel: level, turnId: session.turnId, playback: { active: false, progress: 0 } });
    }, (pcm) => {
      if (generation !== session.generation) return;
      if (recognition?.isReady()) recognition.sendAudio(pcm);
      else pendingAudio.push(pcm);
    });
    session.mode = "listening";
    setStatus("正在连接百度语音识别");
    recognition = createRealtimeRecognition(generation, {
      onText(finalText, partialText) {
        session.acceptedText = finalText || "";
        const shown = String(partialText || finalText || "").trim();
        if (shown) upsertUser(session.turnId, shown, { draft: true });
      },
      onError(message) {
        if (generation !== session.generation || !session.listening) return;
        recorder.cancel();
        session.listening = false;
        fail(generation, message);
      },
    });
    session.recognition = recognition;
    await recognition.ready;
    pendingAudio.splice(0).forEach((pcm) => recognition.sendAudio(pcm));
  } catch (error) {
    recognition?.cancel();
    recorder.cancel();
    micButton.disabled = false;
    fail(generation, error.message);
    return;
  }
  micButton.disabled = false;
  if (generation !== session.generation) {
    recorder.cancel();
    return;
  }
  session.listening = true;
  session.mode = "listening";
  setVoiceState("listening");
  setStatus("说完后再次点击");
}

async function stopAndSend() {
  const generation = session.generation;
  const claimSubmit = session.recognition?.claimSubmit || createSubmitGate();
  session.listening = false;
  enter("thinking", "正在完成语音识别");
  let recording;
  try {
    recording = await recorder.stop();
  } catch (error) {
    fail(generation, error.message);
    return;
  }
  if (generation !== session.generation) return;
  if (recording.durationMs < 400 || recording.maxLevel < 0.008) {
    session.recognition?.cancel();
    session.recognition = null;
    clearDraft();
    fail(generation, "没有识别到有效语音，请重试");
    return;
  }
  let transcript;
  try {
    finishUtterance({
      tail: recording.tail,
      sendAudio: (frame) => session.recognition.sendAudio(frame),
      sendFinish: () => session.recognition.finish(),
    });
    transcript = await session.recognition.completion;
  } catch (error) {
    if (generation !== session.generation) return;
    fail(generation, error.message);
    return;
  } finally {
    session.recognition = null;
  }
  if (generation !== session.generation) return;
  const decision = interpretRecognition(transcript);
  if (decision.action === "retry") {
    clearDraft();
    fail(generation, decision.message);
    return;
  }
  if (!claimSubmit()) return;
  upsertUser(session.turnId, decision.question, { draft: false });
  await answerQuestion(decision.question, generation);
}

async function askFromText(question) {
  await restored;
  cancelCurrent("", false);
  pinToLatest = true;
  const generation = nextGeneration();
  upsertUser(session.turnId, question, { draft: false });
  await answerQuestion(question, generation);
}

async function answerQuestion(question, generation) {
  const turnId = session.turnId;
  try {
    await rememberSession();
  } catch (error) {
    fail(generation, error.message || "没有保存这次对话");
    return;
  }
  if (generation !== session.generation) return;
  enter("thinking", "正在查找资料");
  const node = beginAnswer(turnId);
  const payload = await request("/api/ask", {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ question, session_id: session.sessionId }),
    signal: signalFor(generation),
  }, generation);
  if (!payload || generation !== session.generation || payload.discarded) {
    if (node.isConnected && node.classList.contains("is-pending")) node.remove();
    return;
  }
  session.sessionId = payload.session_id || session.sessionId;
  fillAnswer(node, payload);
  const spoken = (payload.spoken_text || "").trim();
  if (!spoken || !session.ttsReady) {
    enter("responding", spoken ? "正在显示文字回复，当前没有语音播放" : "当前没有可播放的语音");
    stage.render({ mode: "responding", playback: { active: false, progress: 0 }, turnId: session.turnId });
    return;
  }
  await playSpeech(spoken, payload, generation, node);
}

async function playSpeech(text, payload, generation, node) {
  enter("thinking", "正在生成语音");
  let response;
  try {
    response = await fetch("/api/speech/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        session_id: session.sessionId || "",
        turn_id: payload.turn_id || session.turnId,
        epoch: payload.epoch || 0,
      }),
      signal: signalFor(generation),
    });
  } catch (error) {
    if (error.name === "AbortError" || generation !== session.generation) return;
    speechFailed(generation, "无法连接语音合成服务。");
    return;
  }
  if (generation !== session.generation) return;
  if (!response.ok) {
    speechFailed(generation, await readError(response));
    return;
  }
  const blob = await response.blob();
  const type = (response.headers.get("content-type") || blob.type || "").split(";")[0].trim().toLowerCase();
  if (generation !== session.generation) return;
  if (!type.startsWith("audio/") || blob.size < 44) {
    speechFailed(generation, "接口没有返回有效音频。");
    return;
  }
  const url = URL.createObjectURL(blob);
  session.clipUrls.push(url);
  const audio = new Audio(url);
  audio.loop = false;
  audio.preload = "auto";
  session.clips.push(audio);
  session.audio = audio;
  const playback = playbackContext();
  const source = playback.createMediaElementSource(audio);
  const analyser = playback.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  analyser.connect(playback.destination);
  const time = new Uint8Array(analyser.fftSize);
  let frame = 0;
  const paint = () => {
    if (generation !== session.generation || audio.paused) return;
    analyser.getByteTimeDomainData(time);
    const samples = Float32Array.from(time, (value) => (value - 128) / 128);
    const progress = audio.duration ? audio.currentTime / audio.duration : 0;
    stage.render({ mode: "responding", samples, playback: { active: true, progress }, turnId: session.turnId });
    frame = requestAnimationFrame(paint);
  };
  audio.addEventListener("playing", () => {
    pauseOthers(audio);
    if (generation !== session.generation) return;
    session.audio = audio;
    session.mode = "responding";
    setVoiceState("speaking");
    setStatus("正在播放");
    paint();
  });
  const stopVisualization = () => {
    cancelAnimationFrame(frame);
    if (generation !== session.generation) return;
    stage.render({ mode: "responding", playback: { active: false, progress: 1 }, turnId: session.turnId });
  };
  audio.addEventListener("pause", () => {
    if (audio.ended || generation !== session.generation) return;
    stopVisualization();
    if (session.mode !== "responding" || session.audio !== audio) return;
    session.mode = "idle";
    setVoiceState("idle");
    setStatus(audio.currentTime ? "已暂停，可继续播放" : "已停止，可重新播放");
  });
  audio.addEventListener("ended", () => {
    stopVisualization();
    if (generation !== session.generation) return;
    session.mode = "idle";
    setVoiceState("idle");
    setStatus("回答结束");
  });
  audio.addEventListener("error", () => {
    stopVisualization();
    speechFailed(generation, "浏览器无法播放这段音频。");
  });
  const controls = audioControls(audio);
  node.prepend(controls.row);
  try {
    if (playback.state === "suspended") await playback.resume();
    await audio.play();
  } catch (error) {
    stopVisualization();
    controls.play.classList.add("needs-gesture");
    setVoiceState("idle");
    const reason = error && error.name ? error.name : "未知原因";
    setStatus(`浏览器阻止了自动播放（${reason}），请点击“播放回答”`, "speech-error");
  }
}

function speechFailed(generation, detail) {
  if (generation !== session.generation) return;
  const message = `语音失败：${detail}`;
  enter("responding", message);
  setStatus(message, "speech-error");
  stage.render({ mode: "responding", playback: { active: false, progress: 0 }, turnId: session.turnId });
}

function messageNode(turnId, role) {
  return log.querySelector(`[data-message="${turnId}-${role}"]`);
}

function clearChatError() {
  document.getElementById("chat-error")?.remove();
}

function upsertUser(turnId, text, { draft }) {
  clearChatError();
  let node = messageNode(turnId, "user");
  if (!node) {
    node = document.createElement("article");
    node.className = "message user";
    node.dataset.message = `${turnId}-user`;
    node.append(document.createElement("p"));
    log.append(node);
  }
  node.classList.toggle("is-draft", draft);
  node.querySelector("p").textContent = text;
  followLatest();
  return node;
}

function clearDraft() {
  log.querySelectorAll(".message.user.is-draft").forEach((node) => node.remove());
}

function beginAnswer(turnId) {
  let node = messageNode(turnId, "assistant");
  if (!node) {
    node = document.createElement("article");
    node.className = "message assistant";
    node.dataset.message = `${turnId}-assistant`;
    const spoken = document.createElement("p");
    spoken.className = "spoken";
    const notice = document.createElement("p");
    notice.className = "notice";
    node.append(spoken, notice);
    log.append(node);
  }
  node.classList.add("is-pending");
  node.querySelector(".spoken").textContent = "正在思考";
  followLatest();
  return node;
}

function fillAnswer(node, payload) {
  node.classList.remove("is-pending");
  const spoken = (payload.spoken_text || "").trim();
  node.querySelector(".spoken").textContent = spoken || "这一轮没有生成可朗读的人物回答。";
  const notice = node.querySelector(".notice");
  notice.textContent = payload.system_notice || "";
  notice.hidden = !notice.textContent;
  const items = Array.isArray(payload.evidence) ? payload.evidence : [];
  if (items.length && !node.querySelector(".evidence")) {
    const evidence = document.createElement("details");
    evidence.className = "evidence";
    const summary = document.createElement("summary");
    summary.textContent = "查看依据";
    evidence.append(summary);
    items.forEach((item) => evidence.append(evidenceBlock(item)));
    node.append(evidence);
  }
  followLatest();
}

function followLatest() {
  if (!pinToLatest) {
    jumpLatest.hidden = log.scrollHeight <= log.clientHeight + 8;
    return;
  }
  log.scrollTop = log.scrollHeight;
  jumpLatest.hidden = true;
}

function evidenceBlock(item) {
  const block = document.createElement("div");
  const title = document.createElement("p");
  title.textContent = `${item.source_title || "来源未填"} · ${item.page || "页码未填"} · ${item.entry_id || ""}`;
  const quote = document.createElement("p");
  quote.className = "quote";
  quote.textContent = item.original_text || item.quote || "这条没有原文。";
  block.append(title, quote);
  return block;
}

function audioControls(audio) {
  const row = document.createElement("div");
  row.className = "audio-controls";
  const play = document.createElement("button");
  play.type = "button";
  play.className = "quiet-button";
  play.textContent = "播放回答";
  play.addEventListener("click", async () => {
    play.classList.remove("needs-gesture");
    if (!audio.paused) {
      audio.pause();
      return;
    }
    const context = session.playbackContext;
    try {
      if (context && context.state === "suspended") await context.resume();
      await audio.play();
    } catch (error) {
      const reason = error && error.name ? error.name : "未知原因";
      setStatus(`语音失败：点击后仍无法播放（${reason}）`, "speech-error");
    }
  });
  audio.addEventListener("play", () => { play.textContent = "暂停"; });
  audio.addEventListener("pause", () => { play.textContent = "播放回答"; });
  audio.addEventListener("ended", () => { play.textContent = "播放回答"; });

  const stop = document.createElement("button");
  stop.type = "button";
  stop.className = "quiet-button";
  stop.textContent = "停止";
  stop.addEventListener("click", () => {
    audio.pause();
    audio.currentTime = 0;
  });

  const replay = document.createElement("button");
  replay.type = "button";
  replay.className = "quiet-button";
  replay.textContent = "重新播放";
  replay.addEventListener("click", async () => {
    audio.currentTime = 0;
    const context = session.playbackContext;
    try {
      if (context && context.state === "suspended") await context.resume();
      await audio.play();
    } catch (error) {
      const reason = error && error.name ? error.name : "未知原因";
      setStatus(`语音失败：重新播放没有开始（${reason}）`, "speech-error");
    }
  });
  row.append(play, stop, replay);
  return { row, play };
}

function enter(mode, caption) {
  session.mode = mode;
  session.phase = caption;
  if (mode === "listening") setVoiceState("listening");
  else if (mode === "thinking") setVoiceState("thinking");
  else setVoiceState("idle");
  stage.render({
    mode,
    turnId: session.turnId,
    playback: { active: false, progress: 0 },
    samples: null,
    inputLevel: 0,
  });
  setStatus(caption);
}

function fail(generation, message) {
  if (generation !== session.generation) return;
  session.mode = "error";
  session.listening = false;
  micButton.disabled = false;
  clearDraft();
  const pending = messageNode(session.turnId, "assistant");
  if (pending?.classList.contains("is-pending")) pending.remove();
  setVoiceState("idle");
  stage.render({ mode: "error", turnId: session.turnId, playback: { active: false, progress: 0 } });
  const text = message || "这次没有完成，请再试一次。";
  setStatus(text);
  let problem = document.getElementById("chat-error");
  if (!problem) {
    problem = document.createElement("p");
    problem.id = "chat-error";
    problem.className = "chat-error";
    log.append(problem);
  }
  problem.textContent = text;
  followLatest();
}

function cancelCurrent(caption, notifyBackend = true) {
  session.generation += 1;
  session.turnId = `local-${session.generation}`;
  session.controller?.abort();
  session.controller = null;
  session.recognition?.cancel();
  session.recognition = null;
  if (session.listening) recorder.cancel();
  session.listening = false;
  clearDraft();
  micButton.disabled = false;
  setVoiceState("idle");
  if (session.audio) session.audio.pause();
  if (notifyBackend && session.sessionId) {
    fetch(`/api/sessions/${session.sessionId}/cancel`, { method: "POST", headers: apiHeaders() }).catch(() => {});
  }
  if (caption) {
    session.mode = "idle";
    stage.render({ mode: "idle", turnId: session.turnId, playback: { active: false, progress: 0 } });
    setStatus(caption);
  }
}

function releaseClips() {
  session.clips.forEach((clip) => clip.pause());
  session.clips = [];
  session.clipUrls.forEach((url) => URL.revokeObjectURL(url));
  session.clipUrls = [];
  session.audio = null;
  if (session.playbackContext && session.playbackContext.state !== "closed") session.playbackContext.close();
  session.playbackContext = null;
}

async function startNewSession() {
  await restored;
  cancelCurrent("", false);
  releaseClips();
  log.replaceChildren();
  pinToLatest = true;
  jumpLatest.hidden = true;
  try {
    const response = await fetch("/api/sessions", { method: "POST", headers: apiHeaders() });
    const payload = await response.json();
    if (!response.ok || !payload.id) throw new Error("新会话创建失败");
    session.sessionId = payload.id;
    localStorage.setItem(SESSION_KEY, payload.id);
    session.mode = "idle";
    setVoiceState("idle");
    stage.render({ mode: "idle", turnId: session.turnId, playback: { active: false, progress: 0 } });
    setStatus("已开始新对话。之前的对话还在历史里。");
  } catch (error) {
    fail(session.generation, error.message || "新会话创建失败");
  }
}

async function rememberSession() {
  await restored;
  if (session.sessionId) {
    localStorage.setItem(SESSION_KEY, session.sessionId);
    return session.sessionId;
  }
  const response = await fetch("/api/sessions", { method: "POST", headers: apiHeaders() });
  const payload = await response.json();
  if (!response.ok || !payload.id) throw new Error("没有保存这次对话");
  session.sessionId = payload.id;
  localStorage.setItem(SESSION_KEY, payload.id);
  return payload.id;
}

async function restoreConversation({ force = false } = {}) {
  try {
    const saved = localStorage.getItem(SESSION_KEY);
    if (!saved) return;
    const response = await fetch(`/api/sessions/${saved}`, { headers: apiHeaders() });
    if (!force && session.sessionId && session.sessionId !== saved) return;
    if (!response.ok) {
      if (!session.sessionId) {
        localStorage.removeItem(SESSION_KEY);
        session.sessionId = null;
      }
      return;
    }
    const payload = await response.json();
    const sameSession = session.sessionId === payload.session.id;
    session.sessionId = payload.session.id;
    localStorage.setItem(SESSION_KEY, session.sessionId);
    if (!force && sameSession && log.childElementCount) return;
    renderTranscript(payload.turns || []);
  } catch {
    return;
  }
}

function renderTranscript(turns) {
  log.replaceChildren();
  turns.forEach((turn) => {
    if (turn.status === "cancelled" || turn.status === "discarded") return;
    if (turn.question) upsertUser(turn.id, turn.question, { draft: false });
    if (turn.status === "running") {
      beginAnswer(turn.id);
      return;
    }
    if (!turn.spoken_text) return;
    const node = beginAnswer(turn.id);
    fillAnswer(node, { spoken_text: turn.spoken_text, system_notice: "", evidence: [] });
  });
}

async function showHistory() {
  const panel = document.getElementById("history-panel");
  panel.hidden = !panel.hidden;
  if (panel.hidden) return;
  panel.replaceChildren();
  const response = await fetch("/api/sessions", { headers: apiHeaders() });
  const payload = await response.json();
  const items = payload.items || [];
  if (!items.length) {
    panel.textContent = "还没有保存的对话";
    return;
  }
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    if (item.id === session.sessionId) button.classList.add("is-current");
    button.textContent = item.title;
    button.addEventListener("click", () => openSaved(item.id));
    panel.append(button);
  });
}

async function openSaved(sessionId) {
  cancelCurrent("", false);
  releaseClips();
  session.sessionId = sessionId;
  localStorage.setItem(SESSION_KEY, sessionId);
  document.getElementById("history-panel").hidden = true;
  await restoreConversation({ force: true });
  setStatus("已打开保存的对话");
}

async function downloadCurrent() {
  if (!session.sessionId) {
    setStatus("当前还没有可下载的对话");
    return;
  }
  const response = await fetch(`/api/sessions/${session.sessionId}/export`, { headers: apiHeaders() });
  if (!response.ok) {
    setStatus("没有下载到这段对话");
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "对话.txt";
  link.click();
  URL.revokeObjectURL(url);
}

async function deleteCurrent() {
  if (!session.sessionId) return;
  if (!window.confirm("只删除这一段对话。其他历史仍会保留。删除后刷新也不会再出现。")) return;
  const response = await fetch(`/api/sessions/${session.sessionId}`, { method: "DELETE", headers: apiHeaders() });
  if (!response.ok) {
    setStatus("没有删除这段对话");
    return;
  }
  localStorage.removeItem(SESSION_KEY);
  session.sessionId = null;
  log.replaceChildren();
  setStatus("已删除当前对话");
}

async function deleteAllHistory() {
  if (!window.confirm("删除这个浏览器里保存的全部对话。资料和声音不会受影响。删除后无法恢复。")) return;
  const response = await fetch("/api/sessions", { method: "DELETE", headers: apiHeaders() });
  if (!response.ok) {
    setStatus("没有删除历史");
    return;
  }
  localStorage.removeItem(SESSION_KEY);
  session.sessionId = null;
  log.replaceChildren();
  document.getElementById("history-panel").hidden = true;
  setStatus("已删除全部历史");
}

function nextGeneration() {
  session.generation += 1;
  session.turnId = `local-${session.generation}`;
  session.controller?.abort();
  session.controller = new AbortController();
  if (session.audio) session.audio.pause();
  return session.generation;
}

function signalFor(generation) {
  if (generation !== session.generation) return AbortSignal.abort();
  return session.controller.signal;
}

async function request(url, options, generation) {
  try {
    const response = await fetch(url, options);
    if (generation !== session.generation) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "这句话没有发送成功，请再试一次。");
    return data;
  } catch (error) {
    if (error.name === "AbortError") return null;
    fail(generation, error.message || "请求没有成功");
    return null;
  }
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.detail || "语音合成没有成功";
  } catch {
    return "语音合成没有成功";
  }
}

function setStatus(text, state = "") {
  statusLine.textContent = text;
  if (state) statusLine.dataset.state = state;
  else delete statusLine.dataset.state;
}

function setVoiceState(state) {
  const labels = {
    idle: "点击说话",
    listening: "正在聆听，点击结束",
    thinking: "正在思考",
    speaking: "正在播放",
  };
  const label = labels[state] || labels.idle;
  micButton.dataset.state = labels[state] ? state : "idle";
  micButton.setAttribute("aria-label", label);
  voiceCaption.textContent = label;
}

function fitComposer() {
  questionInput.style.height = "auto";
  questionInput.style.height = `${Math.min(questionInput.scrollHeight, 144)}px`;
}

function playbackContext() {
  if (!session.playbackContext || session.playbackContext.state === "closed") {
    session.playbackContext = new AudioContext();
  }
  return session.playbackContext;
}

function pauseOthers(current) {
  session.clips.forEach((clip) => {
    if (clip !== current && !clip.paused) clip.pause();
  });
}

function createRealtimeRecognition(generation, callbacks) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/api/speech/realtime`);
  let readyState = false;
  let settled = false;
  let cancelled = false;
  let readyResolve;
  let readyReject;
  let completeResolve;
  let completeReject;
  let finishTimer = 0;
  const claimSubmit = createSubmitGate();
  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });
  const completion = new Promise((resolve, reject) => {
    completeResolve = resolve;
    completeReject = reject;
  });
  completion.catch(() => {});
  const connectTimer = window.setTimeout(() => fatal("连接百度语音识别超时，请检查网络后重试。"), 15000);

  socket.addEventListener("message", (event) => {
    if (generation !== session.generation || cancelled) return;
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === "READY") {
      window.clearTimeout(connectTimer);
      readyState = true;
      readyResolve();
      return;
    }
    if (message.type === "MID_TEXT") {
      callbacks.onText(message.final_text || "", message.text || "");
      return;
    }
    if (message.type === "CANCELLED") return;
    if (message.type === "FIN_TEXT") {
      callbacks.onText(message.final_text || "", "");
      return;
    }
    if (message.type === "COMPLETE") {
      if (settled) return;
      settled = true;
      window.clearTimeout(finishTimer);
      callbacks.onText(message.corrected_text || message.raw_text || "", "");
      completeResolve(message);
      socket.close(1000);
      return;
    }
    if (message.type === "ERROR") fatal(message.message || "百度实时语音识别发生错误。");
  });
  socket.addEventListener("error", () => fatal("语音识别网络连接失败，请检查网络后重试。"));
  socket.addEventListener("close", () => {
    if (!settled && !cancelled) fatal("语音识别连接已断开，请重新说一次。");
  });

  function fatal(message) {
    if (settled || cancelled) return;
    settled = true;
    window.clearTimeout(connectTimer);
    window.clearTimeout(finishTimer);
    if (!readyState) readyReject(new Error(message));
    completeReject(new Error(message));
    callbacks.onError(message);
    if (socket.readyState === WebSocket.OPEN) socket.close(1011);
  }

  return {
    ready,
    completion,
    isReady: () => readyState && socket.readyState === WebSocket.OPEN && !settled,
    sendAudio(pcm) {
      if (readyState && socket.readyState === WebSocket.OPEN && !settled) socket.send(pcm);
    },
    claimSubmit,
    finish() {
      if (!readyState || socket.readyState !== WebSocket.OPEN || settled) {
        fatal("语音识别连接不可用，请重新说一次。");
        return;
      }
      socket.send(JSON.stringify({ type: "FINISH" }));
      finishTimer = window.setTimeout(() => fatal("等待语音识别最终结果超时，请重新说一次。"), 35000);
    },
    cancel() {
      if (cancelled || settled) return;
      cancelled = true;
      settled = true;
      window.clearTimeout(connectTimer);
      window.clearTimeout(finishTimer);
      if (!readyState) readyReject(new Error("识别已取消。"));
      completeReject(new Error("识别已取消。"));
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "CANCEL" }));
        window.setTimeout(() => socket.close(1000), 50);
      } else {
        socket.close();
      }
    },
  };
}

async function loadModuleStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    session.asrReady = Boolean(data.asr && data.asr.state === "ready" && data.asr.provider === "baidu_realtime");
    session.asrMessage = data.asr?.message || "";
    session.ttsReady = Boolean(data.tts && data.tts.state === "ready");
  } catch {
    session.asrReady = false;
    session.asrMessage = "无法读取语音识别配置状态。";
    session.ttsReady = false;
  }
}
