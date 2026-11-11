"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  settings: null,
  status: null,
  ws: null,
  retry: 0,
  saveTimer: null,
  saving: false,
  dirty: false,
};

const SAVE_IDS = [
  "auto_start",
  "spike_ratio",
  "min_rms",
  "max_gap_s",
  "cooldown_s",
  "open_song",
  "song_uri",
  "focus_cursor",
  "new_cursor_window",
  "cursor_fullscreen",
  "voice_enabled",
  "phrase",
  "voice_delay",
  "voice_id",
  "voice_model",
  "voice_cache",
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail =
        typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return response.json();
}

let toastTimer = null;
function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 5200);
}

function addLog(text, kind = "") {
  const list = $("log");
  const item = document.createElement("li");
  if (kind) item.className = kind;
  const time = document.createElement("span");
  time.className = "t";
  time.textContent = new Date().toLocaleTimeString([], { hour12: false });
  const body = document.createElement("span");
  body.textContent = text;
  item.append(time, body);
  list.prepend(item);
  while (list.children.length > 120) list.lastElementChild.remove();
}

function setValue(id, value) {
  const node = $(id);
  if (node) node.value = value ?? "";
}

function renderLevel(message) {
  const threshold = Number(message.threshold || 0.012);
  const level = Number(message.level || 0);
  const scale = Math.max(threshold * 2.5, 0.05);
  $("meter-fill").style.width = `${Math.min(100, (level / scale) * 100)}%`;
  $("meter-threshold").style.left = `${Math.min(100, (threshold / scale) * 100)}%`;
  $("level-value").textContent = level.toFixed(3);
  $("threshold-value").textContent = threshold.toFixed(3);
  if (message.claps != null) $("claps").textContent = message.claps;
  if (message.doubles != null) $("doubles").textContent = message.doubles;
  const pending = Boolean(message.pending);
  $("pip-1").classList.toggle("on", pending);
  $("stage-text").textContent = pending ? "one more" : "clap twice";
}

function flashPips() {
  $("pip-1").classList.add("fire");
  $("pip-2").classList.add("fire");
  setTimeout(() => {
    $("pip-1").classList.remove("fire");
    $("pip-2").classList.remove("fire");
  }, 650);
}

function applyStatus(data) {
  if (!data) return;
  state.status = data;
  state.settings = data.settings;

  const listener = data.listener || {};
  const listening = Boolean(listener.listening);
  $("pill").dataset.state = listener.error ? "error" : listening ? "on" : "off";
  $("pill-text").textContent = listener.error ? "error" : listening ? "live" : "offline";
  $("toggle").textContent = listening ? "Stop listening" : "Start listening";
  $("device").textContent = listener.device || "no device";
  $("device").title = listener.device || "No input device";
  $("claps").textContent = listener.claps ?? 0;
  $("doubles").textContent = listener.doubles ?? 0;
  $("listener-note").textContent =
    listener.error ||
    (listening
      ? "Listening for a double clap."
      : "Start the listener to arm the microphone.");
  $("version").textContent = data.version || "–";
  $("data-path").textContent = data.paths?.data || "";
  $("key-status").textContent = data.settings.voice.api_key_set
    ? `set (${data.settings.voice.api_key_hint || "saved"})`
    : "not set";

  if (listener.level != null) {
    renderLevel({
      level: listener.level,
      threshold: listener.threshold,
      claps: listener.claps,
      doubles: listener.doubles,
      pending: listener.pending,
    });
  }

  const focused = document.activeElement;
  if (!focused || !focused.closest(".settings")) fillSettings(data.settings);
}

function fillSettings(settings) {
  if (!settings) return;
  $("auto_start").checked = settings.auto_start_listener;

  const audio = settings.audio;
  setValue("spike_ratio", audio.spike_ratio);
  setValue("min_rms", audio.min_rms);
  setValue("max_gap_s", audio.max_gap_s);
  setValue("cooldown_s", audio.cooldown_s);

  const select = $("device-select");
  const wanted = audio.input_device || "";
  if (wanted && ![...select.options].some((option) => option.value === wanted)) {
    const option = document.createElement("option");
    option.value = wanted;
    option.textContent = `${wanted} (unavailable)`;
    select.append(option);
  }
  select.value = wanted;

  const sequence = settings.sequence;
  $("open_song").checked = sequence.open_song;
  setValue("song_uri", sequence.song_uri);
  $("focus_cursor").checked = sequence.focus_cursor;
  $("new_cursor_window").checked = sequence.new_cursor_window;
  $("cursor_fullscreen").checked = sequence.cursor_fullscreen;
  renderTargets(sequence.chrome_targets || []);

  const voice = settings.voice;
  $("voice_enabled").checked = voice.enabled;
  setValue("phrase", voice.phrase);
  setValue("voice_delay", voice.after_song_delay_s);
  setValue("voice_id", voice.voice_id);
  setValue("voice_model", voice.model_id);
  $("voice_cache").checked = voice.cache;
}

function targetRow(target) {
  const row = document.createElement("div");
  row.className = "target";
  row.dataset.id = target.id;
  row.innerHTML = `
    <label class="field"><span>Label</span><input class="t-label" autocomplete="off" /></label>
    <label class="field"><span>URL</span><input class="t-url" type="url" autocomplete="off" /></label>
    <label class="field"><span>Screen</span><input class="t-monitor" type="number" min="1" max="16" /></label>
    <div class="target-actions">
      <label class="check"><input class="t-enabled" type="checkbox" /><span>On</span></label>
      <label class="check"><input class="t-fullscreen" type="checkbox" /><span>Full</span></label>
      <button class="remove" type="button" title="Remove window">&times;</button>
    </div>`;
  row.querySelector(".t-label").value = target.label;
  row.querySelector(".t-url").value = target.url;
  row.querySelector(".t-monitor").value = target.monitor;
  row.querySelector(".t-enabled").checked = target.enabled;
  row.querySelector(".t-fullscreen").checked = target.fullscreen;
  return row;
}

function renderTargets(targets) {
  const wrap = $("targets");
  wrap.replaceChildren();
  if (!targets.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No Chrome windows configured.";
    wrap.append(empty);
    return;
  }
  for (const target of targets) wrap.append(targetRow(target));
}

function collectTargets() {
  return [...document.querySelectorAll("#targets .target")]
    .map((row) => ({
      id: row.dataset.id,
      label: row.querySelector(".t-label").value.trim() || "Window",
      url: row.querySelector(".t-url").value.trim(),
      enabled: row.querySelector(".t-enabled").checked,
      monitor: Math.max(1, parseInt(row.querySelector(".t-monitor").value, 10) || 1),
      fullscreen: row.querySelector(".t-fullscreen").checked,
    }))
    .filter((target) => target.url.length > 0);
}

function collectSettings() {
  const base = JSON.parse(JSON.stringify(state.settings));
  base.auto_start_listener = $("auto_start").checked;
  base.audio = {
    ...base.audio,
    input_device: $("device-select").value,
    spike_ratio: Number($("spike_ratio").value),
    min_rms: Number($("min_rms").value),
    max_gap_s: Number($("max_gap_s").value),
    cooldown_s: Number($("cooldown_s").value),
  };
  base.sequence = {
    ...base.sequence,
    open_song: $("open_song").checked,
    song_uri: $("song_uri").value.trim(),
    chrome_targets: collectTargets(),
    focus_cursor: $("focus_cursor").checked,
    new_cursor_window: $("new_cursor_window").checked,
    cursor_fullscreen: $("cursor_fullscreen").checked,
  };
  base.voice = {
    ...base.voice,
    enabled: $("voice_enabled").checked,
    phrase: $("phrase").value,
    after_song_delay_s: Number($("voice_delay").value),
    voice_id: $("voice_id").value.trim(),
    model_id: $("voice_model").value.trim(),
    cache: $("voice_cache").checked,
  };
  return base;
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(save, 450);
}

async function save() {
  if (!state.settings) return;
  if (state.saving) {
    state.dirty = true;
    return;
  }
  state.saving = true;
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(collectSettings()),
    });
    if (data.status) applyStatus(data.status);
    $("saved").classList.add("show");
    setTimeout(() => $("saved").classList.remove("show"), 1200);
  } catch (error) {
    toast(error.message);
  } finally {
    state.saving = false;
    if (state.dirty) {
      state.dirty = false;
      scheduleSave();
    }
  }
}

async function refresh() {
  try {
    applyStatus(await api("/api/status"));
  } catch (error) {
    toast(error.message);
  }
}

async function loadDevices() {
  try {
    const data = await api("/api/devices");
    const select = $("device-select");
    const current = select.value;
    select.replaceChildren(new Option("default / auto", ""));
    for (const device of data.devices || []) {
      const suffix = device.default ? " (default)" : "";
      select.append(new Option(`[${device.index}] ${device.name}${suffix}`, String(device.index)));
    }
    if (current) select.value = current;
    if (data.error) addLog(`Audio unavailable: ${data.error}`, "error");
  } catch (error) {
    addLog(`Could not list audio devices: ${error.message}`, "error");
  }
}

async function toggleListener() {
  const listening = Boolean(state.status?.listener?.listening);
  try {
    const data = await api(listening ? "/api/listener/stop" : "/api/listener/start", {
      method: "POST",
    });
    if (data.error) toast(data.error);
    if (data.listener) {
      applyStatus({
        ...state.status,
        listener: data.listener,
      });
    }
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

async function triggerSequence() {
  try {
    await api("/api/trigger", { method: "POST" });
    $("last-trigger").textContent = new Date().toLocaleTimeString([], { hour12: false });
    addLog("Manual sequence triggered", "seq");
  } catch (error) {
    toast(error.message);
  }
}

async function saveKey() {
  const input = $("api_key");
  const key = input.value.trim();
  if (!key) {
    toast("Enter an API key first.");
    return;
  }
  try {
    await api("/api/voice/key", {
      method: "PUT",
      body: JSON.stringify({ api_key: key }),
    });
    input.value = "";
    addLog("ElevenLabs key saved");
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

async function clearKey() {
  try {
    await api("/api/voice/key", { method: "DELETE" });
    addLog("ElevenLabs key cleared");
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

async function previewVoice() {
  try {
    await api("/api/voice/preview", { method: "POST" });
    addLog("Voice preview requested");
  } catch (error) {
    toast(error.message);
  }
}

function handleEvent(message) {
  switch (message.type) {
    case "snapshot":
      applyStatus(message.status);
      $("log").replaceChildren();
      (message.events || [])
        .filter((event) => event.type !== "level")
        .slice(-30)
        .forEach((event) => addLog(describe(event), kindOf(event)));
      break;
    case "level":
      renderLevel(message);
      break;
    case "clap":
      $("pip-1").classList.add("on");
      addLog(`Clap (level ${Number(message.level).toFixed(3)})`, "clap");
      break;
    case "double-clap":
      flashPips();
      $("last-trigger").textContent = new Date().toLocaleTimeString([], { hour12: false });
      addLog(`Double clap — gap ${Number(message.gap).toFixed(2)}s`, "double");
      break;
    case "action":
      addLog(`${message.step}: ${message.detail || message.status}`, message.status === "error" ? "error" : "");
      break;
    case "sequence":
      addLog(
        message.state === "started"
          ? `Sequence started (${message.source})`
          : `Sequence finished in ${message.seconds}s`,
        "seq"
      );
      break;
    case "listener":
      if (message.state === "error") toast(message.error);
      refresh();
      break;
    case "ping":
      break;
    default:
      break;
  }
}

function describe(event) {
  switch (event.type) {
    case "action":
      return `${event.step}: ${event.detail || event.status}`;
    case "sequence":
      return event.state === "started" ? "Sequence started" : "Sequence finished";
    case "double-clap":
      return "Double clap";
    case "clap":
      return "Clap";
    case "listener":
      return `Listener ${event.state}${event.error ? `: ${event.error}` : ""}`;
    default:
      return event.type;
  }
}

function kindOf(event) {
  if (event.type === "double-clap") return "double";
  if (event.type === "sequence") return "seq";
  if (event.type === "action" && event.status === "error") return "error";
  if (event.type === "listener" && event.state === "error") return "error";
  return "";
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  state.ws = socket;
  socket.addEventListener("open", () => {
    state.retry = 0;
  });
  socket.addEventListener("message", (event) => {
    try {
      handleEvent(JSON.parse(event.data));
    } catch (_) {
      /* ignore malformed frames */
    }
  });
  socket.addEventListener("close", () => {
    const delay = Math.min(5000, 500 * 2 ** state.retry);
    state.retry += 1;
    setTimeout(connect, delay);
  });
  socket.addEventListener("error", () => socket.close());
}

function bind() {
  for (const id of SAVE_IDS) {
    const node = $(id);
    if (!node) continue;
    const event = node.tagName === "SELECT" || node.type === "checkbox" ? "change" : "input";
    node.addEventListener(event, scheduleSave);
  }

  $("device-select").addEventListener("change", scheduleSave);
  $("toggle").addEventListener("click", toggleListener);
  $("trigger").addEventListener("click", triggerSequence);
  $("save-key").addEventListener("click", saveKey);
  $("clear-key").addEventListener("click", clearKey);
  $("preview-voice").addEventListener("click", previewVoice);

  $("add-target").addEventListener("click", () => {
    const row = targetRow({
      id: `target-${Date.now().toString(36)}`,
      label: "New window",
      url: "https://example.com",
      enabled: false,
      monitor: 1,
      fullscreen: false,
    });
    const wrap = $("targets");
    const empty = wrap.querySelector("p");
    if (empty) empty.remove();
    wrap.append(row);
    scheduleSave();
  });

  $("targets").addEventListener("input", scheduleSave);
  $("targets").addEventListener("change", scheduleSave);
  $("targets").addEventListener("click", (event) => {
    const button = event.target.closest(".remove");
    if (!button) return;
    button.closest(".target").remove();
    if (!document.querySelectorAll("#targets .target").length) renderTargets([]);
    scheduleSave();
  });

  $("api_key").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveKey();
    }
  });
}

async function init() {
  bind();
  await loadDevices();
  await refresh();
  connect();
}

init();
