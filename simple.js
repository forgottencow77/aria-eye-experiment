const ui = Object.fromEntries(["connection", "error", "gazePad", "gazeX", "gazeY", "xValue", "yValue", "openness", "lidValue", "irisColor", "colorValue", "finish", "release", "activity", "center", "openLids", "halfLids", "closeLids", "defaultColor", "advancedLink"].map(id => [id, document.getElementById(id)]));
const pose = {x: 0, y: 0, openness: 1};
let irisColor = null;
let token = null;
let sequence = 0;
let pending = null;
let sending = false;
let generation = 0;
let chain = Promise.resolve();
let pointerId = null;
let ready = false;
let pollBusy = false;
let heartbeatBusy = false;
const clamp = (v, low, high) => Math.max(low, Math.min(high, v));

function serial(action) {
  const result = chain.then(action);
  chain = result.catch(() => {});
  return result;
}

async function request(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4500);
  try {
    const response = await fetch(path, {signal: controller.signal,
      ...(body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  } finally { clearTimeout(timer); }
}

function error(error) {
  ui.error.textContent = error.message || String(error);
  ui.error.hidden = false;
  ui.connection.textContent = "操作を確認してください";
  ui.connection.className = "connection bad";
}

function render() {
  const style = document.documentElement.style;
  style.setProperty("--gx", pose.x);
  style.setProperty("--gy", pose.y);
  style.setProperty("--open", pose.openness);
  style.setProperty("--iris", irisColor || "#EAA636");
  ui.gazeX.value = Math.round(pose.x * 100);
  ui.gazeY.value = Math.round(pose.y * 100);
  ui.openness.value = Math.round(pose.openness * 100);
  ui.xValue.textContent = Math.abs(pose.x) < 0.01 ? "正面" : `${pose.x < 0 ? "左" : "右"} ${Math.round(Math.abs(pose.x)*100)}%`;
  ui.yValue.textContent = Math.abs(pose.y) < 0.01 ? "正面" : `${pose.y < 0 ? "下" : "上"} ${Math.round(Math.abs(pose.y)*100)}%`;
  ui.lidValue.innerHTML = `${Math.round(pose.openness * 100)}<span>%</span>`;
  ui.irisColor.value = irisColor || "#EAA636";
  ui.colorValue.textContent = irisColor || "標準の色";
  document.querySelectorAll("[data-color]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.color === irisColor)));
  ui.finish.disabled = !token;
}

async function ensureSession(epoch) {
  if (token) return;
  const started = await request("/api/live/start", {});
  if (epoch !== generation) {
    await request("/api/live/stop", {token: started.token});
    return;
  }
  token = started.token;
  sequence = 0;
  ui.activity.textContent = "操作中 · 指を離すとその位置で止まります。";
  render();
}

function queue(update) {
  if (!ready) return;
  pending = {...pending, ...update};
  ui.error.hidden = true;
  render();
}

function failControl(problem) {
  token = null;
  pending = null;
  generation++;
  ui.activity.textContent = "操作を中断しました。接続を確認して、もう一度操作してください。";
  render();
  error(problem);
}

async function flush() {
  if (sending || !pending || document.hidden) return;
  const update = pending;
  pending = null;
  const epoch = generation;
  sending = true;
  try {
    await serial(async () => {
      if (epoch !== generation) return;
      await ensureSession(epoch);
      if (epoch !== generation || !token) return;
      await request("/api/live/input", {token, sequence: ++sequence, ...update});
      ui.connection.textContent = "実機に接続済み";
      ui.connection.className = "connection ready";
    });
  } catch (problem) { failControl(problem); }
  finally { sending = false; }
}

async function stop(release = false) {
  generation++;
  pending = null;
  pointerId = null;
  ui.gazePad.classList.remove("dragging");
  try {
    await serial(async () => {
      if (token || release) await request("/api/live/stop", {token, release});
      token = null;
    });
    ui.activity.textContent = release ? "眼球・まぶたの出力を解除しました。" : "操作を終了しました。";
    render();
  } catch (problem) { failControl(problem); }
}

function sendPose() { queue({pose: {...pose}}); }
function setOpen(value) { pose.openness = value; sendPose(); }
function drag(event) {
  const rect = ui.gazePad.getBoundingClientRect();
  pose.x = clamp((event.clientX - rect.left) / rect.width * 2 - 1, -1, 1);
  pose.y = clamp(1 - (event.clientY - rect.top) / rect.height * 2, -1, 1);
  sendPose();
}
ui.gazePad.addEventListener("pointerdown", event => {
  if (!ready || (event.pointerType === "mouse" && event.button !== 0)) return;
  pointerId = event.pointerId;
  ui.gazePad.setPointerCapture(pointerId);
  ui.gazePad.classList.add("dragging");
  drag(event);
});
ui.gazePad.addEventListener("pointermove", event => { if (event.pointerId === pointerId) drag(event); });
for (const eventName of ["pointerup", "pointercancel", "lostpointercapture"]) {
  ui.gazePad.addEventListener(eventName, event => {
    if (event.pointerId === pointerId) { pointerId = null; ui.gazePad.classList.remove("dragging"); }
  });
}
ui.gazePad.addEventListener("keydown", event => {
  const keys = {ArrowLeft: [-0.05, 0], ArrowRight: [0.05, 0], ArrowUp: [0, 0.05], ArrowDown: [0, -0.05]};
  if (event.key === "Home") { event.preventDefault(); pose.x = pose.y = 0; sendPose(); }
  else if (keys[event.key]) {
    event.preventDefault();
    pose.x = clamp(pose.x + keys[event.key][0], -1, 1);
    pose.y = clamp(pose.y + keys[event.key][1], -1, 1);
    sendPose();
  }
});
ui.gazeX.addEventListener("input", () => { pose.x = Number(ui.gazeX.value)/100; sendPose(); });
ui.gazeY.addEventListener("input", () => { pose.y = Number(ui.gazeY.value)/100; sendPose(); });
ui.openness.addEventListener("input", () => setOpen(Number(ui.openness.value)/100));
ui.center.addEventListener("click", () => { pose.x = pose.y = 0; sendPose(); });
ui.openLids.addEventListener("click", () => setOpen(1));
ui.halfLids.addEventListener("click", () => setOpen(0.5));
ui.closeLids.addEventListener("click", () => setOpen(0));
function setColor(color) { irisColor = color; queue({irisColor: color}); }
ui.irisColor.addEventListener("input", () => setColor(ui.irisColor.value.toUpperCase()));
document.querySelectorAll("[data-color]").forEach(button => button.addEventListener("click", () => setColor(button.dataset.color)));
ui.defaultColor.addEventListener("click", () => setColor(null));
ui.finish.addEventListener("click", () => void stop());
ui.release.addEventListener("click", () => void stop(true));
document.addEventListener("keydown", event => { if (event.key === "Escape") { event.preventDefault(); void stop(true); } });
ui.advancedLink.addEventListener("click", async event => {
  event.preventDefault();
  await stop();
  location.href = "/";
});
function leave() {
  generation++;
  pending = null;
  if (token) {
    const old = token;
    token = null;
    fetch("/api/live/stop", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: old}), keepalive: true}).catch(() => {});
  }
  render();
}
window.addEventListener("pagehide", leave);
document.addEventListener("visibilitychange", () => { if (document.hidden) leave(); else void poll(); });
setInterval(() => void flush(), 65);
setInterval(() => {
  if (!token || sending || pending || heartbeatBusy || document.hidden) return;
  heartbeatBusy = true;
  const epoch = generation;
  void serial(async () => {
    if (epoch === generation && token) await request("/api/live/keepalive", {token});
  }).catch(failControl).finally(() => { heartbeatBusy = false; });
}, 450);
async function poll() {
  if (pollBusy || token || pending || sending || document.hidden) return;
  pollBusy = true;
  try {
    const state = await request("/api/live/status");
    ready = true;
    if (!token && !pending && !sending) {
      Object.assign(pose, state.pose);
      irisColor = state.irisColor;
      render();
      ui.connection.textContent = state.active ? "別の画面で操作中" : "実機に接続済み";
      ui.connection.className = "connection ready";
    }
  } catch (problem) { ready = false; error(problem); }
  finally { pollBusy = false; }
}
setInterval(() => void poll(), 1800);
render();
void poll();
