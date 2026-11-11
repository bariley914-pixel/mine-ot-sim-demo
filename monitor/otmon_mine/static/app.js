/* otmon-mine dashboard + HMI. Vanilla JS, no external libraries, no CDN (works offline). */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const SEV = {critical: 0, high: 1, medium: 2, low: 3, info: 4};
let S = null;                 // last /api/status
let HIST = {};                // key -> [[ts, v], ...]
let route = "overview", routeArg = null;
let trendSel = null, trendSecs = 600, evFilter = "all";
const COLORS = ["#60a5fa", "#f5a524", "#22c55e", "#e879f9", "#38bdf8", "#f87171"];

/* ---------------- API ---------------- */
async function api(path, body) {
  const opt = body === undefined ? {cache: "no-store"} :
    {method: "POST", headers: {"Content-Type": "application/json", "X-OTMon-HMI": "1"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok && body !== undefined) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
function toast(msg, err) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  $("#toasts").appendChild(t); setTimeout(() => t.remove(), err ? 7000 : 3500);
}
function confirmBox(title, text) {
  return new Promise(res => {
    $("#modalTitle").textContent = title; $("#modalText").textContent = text;
    $("#modal").classList.remove("hidden");
    const done = v => { $("#modal").classList.add("hidden"); $("#modalOk").onclick = $("#modalCancel").onclick = null; res(v); };
    $("#modalOk").onclick = () => done(true); $("#modalCancel").onclick = () => done(false);
  });
}
async function doWrite(dev, point, value, label, confirmText) {
  if (S && !S.health.hmi_writes_enabled) { toast("HMI writes are disabled (read-only monitor mode)", true); return; }
  if (confirmText && !(await confirmBox(label, confirmText))) return;
  try { await api("/api/write", {device: dev, point, value}); toast(`✔ ${label} sent to ${dev}`); setTimeout(refresh, 300); }
  catch (e) { toast(`✖ ${label}: ${e.message}`, true); }
}
async function scenario(action) {
  const txt = {reset: "Restart every simulated device's scenario clock and restore default commands/setpoints. Active alarms are acknowledged.",
    auto_on: "Turn ON the automatic fault scenarios on every device.", auto_off: "Turn OFF the automatic fault scenarios. The plant runs normally until you inject a fault.",
    rogue: "ATTACK DEMO: write CV-101 speed setpoint = 150 % directly over Modbus, bypassing this HMI (as an unauthorized engineering laptop would). The monitor should flag R-WR-001 and R-RNG-003. Use Reset simulation to undo."}[action];
  if (!(await confirmBox({reset: "Reset simulation", rogue: "Simulate rogue Modbus write"}[action] || "Fault scenarios", txt))) return;
  try { await api("/api/scenario", {action}); toast("✔ Scenario: " + action.replace("_", " ")); setTimeout(refresh, 300); }
  catch (e) { toast("✖ " + e.message, true); }
}
async function ack(key) {
  try { const r = await api("/api/ack", key ? {key} : {}); toast(`✔ ${r.acked} alarm(s) acknowledged`); refresh(); }
  catch (e) { toast("✖ " + e.message, true); }
}

/* ---------------- helpers ---------------- */
const devById = id => S && S.devices.find(d => d.id === id);
const pt = (d, id) => d.points.find(p => p.id === id);
function fmtv(v, p) {
  if (v == null) return "–";
  if (p && (p.type === "discrete" || p.type === "coil")) return v ? "ON" : "OFF";
  const dec = p ? Math.min(p.scale || 0, 2) : 1;
  return Number(v).toLocaleString(undefined, {minimumFractionDigits: dec, maximumFractionDigits: dec});
}
function alarmsFor(dev, point) { return (S ? S.alarms : []).filter(a => a.active && a.device === dev && (!point || a.point === point)); }
function sevClass(list) { if (!list.length) return ""; return list.some(a => a.severity === "critical") ? "crit" : "alm"; }
function limitsFor(d, pid) {
  const r = (d.alarm_rules || []).find(x => x.point === pid) || {};
  return ["hihi", "hi", "lo", "lolo"].filter(k => r[k] != null).map(k => ({k, v: r[k]}));
}
const tsLocal = iso => iso ? new Date(iso).toLocaleTimeString() : "";
const stateDot = s => ({RUNNING: "#22c55e", ONLINE: "#22c55e", STOPPED: "#94a3b8", TRIPPED: "#ef4444", "COMM LOSS": "#ef4444", CONNECTING: "#f5a524"}[s] || "#94a3b8");

/* ---------------- chart (canvas) ---------------- */
function drawChart(cv, series, opt = {}) {
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  const pad = opt.spark ? {l: 2, r: 2, t: 4, b: 4} : {l: 46, r: 12, t: 8, b: 22};
  const now = Date.now() / 1000, x0 = now - (opt.secs || 600);
  let lo = Infinity, hi = -Infinity;
  series.forEach(s => s.data.forEach(([t, v]) => { if (t >= x0) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
  (opt.limits || []).forEach(l => { lo = Math.min(lo, l.v); hi = Math.max(hi, l.v); });
  if (!isFinite(lo)) { g.fillStyle = "#8796a8"; g.font = "12px system-ui"; g.fillText(opt.spark ? "" : "waiting for data…", pad.l + 8, H / 2); return; }
  if (opt.min0 && lo > 0) lo = 0;
  if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
  const span = hi - lo; hi += span * 0.08; lo -= opt.min0 && lo >= 0 ? 0 : span * 0.08;
  const X = t => pad.l + (t - x0) / (now - x0) * (W - pad.l - pad.r), Y = v => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
  if (!opt.spark) {
    g.strokeStyle = "#223040"; g.fillStyle = "#8796a8"; g.font = "11px system-ui"; g.lineWidth = 1;
    for (let i = 0; i <= 4; i++) { const v = lo + (hi - lo) * i / 4, y = Y(v); g.beginPath(); g.moveTo(pad.l, y); g.lineTo(W - pad.r, y); g.stroke();
      g.fillText(Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2), 4, y + 4); }
    const nt = W < 500 ? 3 : 4;
    for (let i = 0; i <= nt; i++) { const t = x0 + (now - x0) * i / nt, x = X(t);
      g.textAlign = i === 0 ? "left" : i === nt ? "right" : "center";
      g.fillText(new Date(t * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}), i === nt ? W - pad.r : x, H - 6); }
    g.textAlign = "left";
  }
  (opt.limits || []).forEach(l => { g.strokeStyle = l.k.length === 4 ? "#ef4444" : "#f97316"; g.setLineDash([5, 4]); g.lineWidth = 1;
    g.beginPath(); g.moveTo(pad.l, Y(l.v)); g.lineTo(W - pad.r, Y(l.v)); g.stroke(); g.setLineDash([]);
    if (!opt.spark) { g.fillStyle = g.strokeStyle; g.fillText(l.k.toUpperCase() + " " + l.v, W - pad.r - 64, Y(l.v) - 3); } });
  series.forEach((s, i) => {
    const pts = s.data.filter(d => d[0] >= x0); if (!pts.length) return;
    g.strokeStyle = s.color || COLORS[i % COLORS.length]; g.lineWidth = opt.spark ? 1.5 : 2; g.beginPath();
    pts.forEach(([t, v], j) => j ? g.lineTo(X(t), Y(v)) : g.moveTo(X(t), Y(v))); g.stroke();
    if (opt.spark) { g.lineTo(X(pts[pts.length - 1][0]), H); g.lineTo(X(pts[0][0]), H); g.closePath(); g.globalAlpha = .12; g.fillStyle = g.strokeStyle; g.fill(); g.globalAlpha = 1; }
  });
  cv._map = {X, Y, x0, now, series, pad, W, H};
}
function attachHover(cv) {
  cv.onmousemove = e => {
    const m = cv._map; if (!m) return; const r = cv.getBoundingClientRect(), x = e.clientX - r.left;
    const t = m.x0 + (x - m.pad.l) / (m.W - m.pad.l - m.pad.r) * (m.now - m.x0);
    drawChart(cv, m.series, cv._opt); const g = cv.getContext("2d"); const dpr = window.devicePixelRatio || 1; g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.strokeStyle = "#ffffff55"; g.beginPath(); g.moveTo(x, m.pad.t); g.lineTo(x, m.H - m.pad.b); g.stroke();
    let label = new Date(t * 1000).toLocaleTimeString();
    m.series.forEach(s => { let best = null; s.data.forEach(d => { if (!best || Math.abs(d[0] - t) < Math.abs(best[0] - t)) best = d; });
      if (best) label += `  ${s.name}: ${best[1]}`; });
    g.font = "12px system-ui"; const w = g.measureText(label).width + 12; const bx = Math.min(x + 8, m.W - w - 4);
    g.fillStyle = "#0b1016ee"; g.fillRect(bx, m.pad.t + 2, w, 20); g.fillStyle = "#fff"; g.fillText(label, bx + 6, m.pad.t + 16);
  };
  cv.onmouseleave = () => cv._map && drawChart(cv, cv._map.series, cv._opt);
}
async function loadHist(keys, secs) {
  if (!keys.length) return;
  const h = await api(`/api/history?keys=${encodeURIComponent(keys.join(","))}&seconds=${secs}`);
  Object.assign(HIST, h);
}

/* ---------------- header ---------------- */
function renderHeader() {
  $("#site").textContent = S.site;
  const act = S.alarms.filter(a => a.active), un = S.alarms.filter(a => !a.acked);
  const c = s => act.filter(a => a.severity === s).length;
  $("#counters").innerHTML = ["critical", "high", "medium"].map(s => `<span class="cnt ${s} ${c(s) ? "on" : ""} ${un.some(a => a.severity === s) ? "unack" : ""}">${c(s)} ${s}</span>`).join("")
    + `<span class="cnt">${un.length} unacked</span>`;
  const h = S.health;
  $("#comms").className = "pill " + (h.devices_online === h.devices_total ? "ok" : "bad");
  $("#comms").textContent = `comms ${h.devices_online}/${h.devices_total}`;
  $("#hmimode").className = "pill " + (h.hmi_writes_enabled ? "warn" : "ok");
  $("#hmimode").textContent = h.hmi_writes_enabled ? "HMI control: ON" : "read-only monitor";
  $("#navAlarmBadge").textContent = act.length || "";
  const top = S.alarms.filter(a => a.active && !a.acked).sort((a, b) => SEV[a.severity] - SEV[b.severity])[0];
  const b = $("#banner");
  if (top) { b.className = `banner ${top.severity} flash`; b.innerHTML = `<span>⚠ ${esc(top.severity.toUpperCase())}</span><span>${esc(top.label)}</span><span class="spacer"></span><span>${esc(top.device_name)} · ${tsLocal(top.raised_at)}</span><button class="btn small" id="bannerAck">Ack</button>`;
    $("#bannerAck").onclick = ev => { ev.stopPropagation(); ack(top.key); }; b.onclick = () => location.hash = "#/alarms"; }
  else b.className = "banner hidden";
  $("#navDevices").innerHTML = S.devices.map(d => `<a href="#/device/${d.id}" data-r="device/${d.id}"><span class="dot" style="background:${stateDot(d.state)}"></span>${esc(d.name.split(" ")[0])} <span class="muted">${esc(d.kind)}</span>${alarmsFor(d.id).length ? `<b class="badge">${alarmsFor(d.id).length}</b>` : ""}</a>`).join("");
  document.querySelectorAll(".side a").forEach(a => a.classList.toggle("active", a.dataset.r === (routeArg ? route + "/" + routeArg : route)));
}

/* ---------------- views ---------------- */
function kpiTile(dev, pid, label) {
  const d = devById(dev); if (!d) return ""; const p = pt(d, pid); const v = d.values[pid]; const cls = sevClass(alarmsFor(dev, pid));
  return `<div class="kpi ${cls}"><div class="l">${esc(label || p.label)}</div><div class="v">${fmtv(v, p)}<small>${esc(p.units || "")}</small></div></div>`;
}
function ctlButtons(d, list, cls) {
  return list.map((c, i) => c.kind === "setpoint" ? "" :
    `<button class="btn small ${cls || c.style || ""}" data-dev="${d.id}" data-idx="${i}" data-list="${cls === "fault" ? "faults" : "controls"}" ${S.health.hmi_writes_enabled && d.comm.ok ? "" : "disabled"}>${esc(c.label)}</button>`).join("");
}
function bindCtl(root) {
  root.querySelectorAll("button[data-dev]").forEach(b => b.onclick = () => {
    const d = devById(b.dataset.dev), c = d[b.dataset.list][+b.dataset.idx];
    doWrite(d.id, c.point, c.value, `${d.name.split(" ")[0]}: ${c.label}`, c.confirm || null);
  });
}
function mimic() {
  const v = (dev, p) => { const d = devById(dev); return d ? d.values[p] : null; };
  const st = dev => (devById(dev) || {}).state;
  const col = s => stateDot(s);
  const cb = (x, y, closed, label) => `<rect x="${x - 11}" y="${y - 11}" width="22" height="22" rx="3" fill="${closed ? "#b91c1c" : "#14532d"}" stroke="#fff" stroke-width="1.2"/><text x="${x + 18}" y="${y + 4}" class="big">${label} ${closed ? "CLOSED" : "OPEN"}</text>`;
  const cb1 = v("rtu-sub-01", "cb1_closed"), cb2 = v("rtu-sub-01", "cb2_closed");
  const live1 = cb1 ? "#f5a524" : "#475569", live2 = cb1 && cb2 ? "#f5a524" : "#475569";
  const box = (x, y, dev, title, line2) => `<g class="mbox" data-go="${dev}"><rect x="${x}" y="${y}" width="190" height="62" rx="8" fill="#151c25" stroke="${col(st(dev))}" stroke-width="2"/><circle cx="${x + 176}" cy="${y + 14}" r="6" fill="${col(st(dev))}"/><text x="${x + 10}" y="${y + 20}" class="big">${title}</text><text x="${x + 10}" y="${y + 40}">${line2}</text><text x="${x + 10}" y="${y + 55}" fill="#8796a8">${esc(st(dev) || "")}</text></g>`;
  const f = (x, n = 1) => x == null ? "–" : Number(x).toFixed(n);
  return `<svg viewBox="0 0 980 300" class="mimic" preserveAspectRatio="xMidYMid meet">
   <text x="20" y="22" class="big">Utility 13.8 kV</text><line x1="70" y1="30" x2="70" y2="70" stroke="#f5a524" stroke-width="3"/>
   ${cb(70, 82, cb1, "CB-1")}<line x1="70" y1="93" x2="70" y2="130" stroke="${live1}" stroke-width="3"/>
   <line x1="40" y1="130" x2="940" y2="130" stroke="${live1}" stroke-width="5"/><text x="150" y="122">Surface bus · ${f(v("rtu-sub-01", "voltage_kv"), 2)} kV · ${f(v("rtu-sub-01", "total_kw"), 0)} kW</text>
   <line x1="760" y1="130" x2="760" y2="170" stroke="${live1}" stroke-width="3"/>${box(665, 170, "plc-vent-01", "F-301 Main fan", `${f(v("plc-vent-01", "airflow"))} m³/s · vib ${f(v("plc-vent-01", "vibration"), 2)} mm/s`)}
   <line x1="330" y1="130" x2="330" y2="158" stroke="${live1}" stroke-width="3"/>${cb(330, 170, cb2, "CB-2")}<line x1="330" y1="181" x2="330" y2="205" stroke="${live2}" stroke-width="3"/>
   <line x1="120" y1="205" x2="540" y2="205" stroke="${live2}" stroke-width="4"/><text x="380" y="198">Underground section · ${f(v("rtu-sub-01", "section_kw"), 0)} kW</text>
   <line x1="160" y1="205" x2="160" y2="225" stroke="${live2}" stroke-width="3"/>${box(40, 225, "plc-conveyor-01", "CV-101 Conveyor", `${f(v("plc-conveyor-01", "load_tph"), 0)} t/h · ${f(v("plc-conveyor-01", "motor_current"), 0)} A`)}
   <line x1="430" y1="205" x2="430" y2="225" stroke="${live2}" stroke-width="3"/>${box(320, 225, "plc-dewater-01", "P-201 Sump pump", `level ${f(v("plc-dewater-01", "sump_level"))} % · ${f(v("plc-dewater-01", "outflow"))} L/s`)}
   ${box(665, 30, "rtu-gas-01", "GM-401 Gas (return)", `CH₄ ${f(v("rtu-gas-01", "ch4"), 2)} % · CO ${f(v("rtu-gas-01", "co"))} ppm`)}
   <path d="M840 168 C 900 150, 900 110, 855 92" stroke="#38bdf8" stroke-dasharray="4 4" fill="none"/><text x="800" y="112" fill="#38bdf8">airflow → CH₄ dilution</text>
   ${v("rtu-sub-01", "gas_interlock") ? `<text x="380" y="168" fill="#ef4444" class="big">⚡ CH₄ INTERLOCK TRIPPED CB-2</text>` : ""}
  </svg>`;
}
function viewOverview(el) {
  const anyAuto = S.devices.some(d => d.values.auto_faults === 1);
  el.innerHTML = `<div class="row"><div><h1>Mine overview</h1><p class="sub">Live telemetry polled read-only over Modbus TCP from ${S.devices.length} simulated ot-sim devices · updated ${tsLocal(S.generated_at)}</p></div></div>
  <div class="kpis">${kpiTile("rtu-sub-01", "total_kw", "Mine load")}${kpiTile("rtu-sub-01", "voltage_kv", "Bus voltage")}${kpiTile("rtu-gas-01", "ch4", "Methane (return)")}
   ${kpiTile("plc-dewater-01", "sump_level", "Sump level")}${kpiTile("plc-vent-01", "airflow", "Main fan airflow")}${kpiTile("plc-conveyor-01", "load_tph", "Belt load")}</div>
  <div class="grid g21"><div class="panel"><h2>Process &amp; power one-line <span class="muted">(click a device)</span></h2>${mimic()}</div>
  <div class="panel scen"><h2>Simulation scenario</h2>
   <div class="toggle"><button class="sw ${anyAuto ? "on" : ""}" id="swAuto" title="Automatic fault scenarios"></button><div><b>Automatic fault scenarios</b><div class="muted">Each device cycles through a realistic fault every 2-4 minutes so alarms fire unattended.</div></div></div>
   <div><div class="muted">Inject a fault now:</div><div class="btns" id="faultBtns">${S.devices.map(d => ctlButtons(d, d.faults || [], "fault")).join("")}</div></div>
   <div class="row"><button class="btn primary" id="btnReset">↺ Reset simulation</button><span class="muted">Restarts every scenario clock and restores default setpoints.</span></div>
   <div class="row"><button class="btn danger" id="btnRogue">☠ Simulate rogue setpoint write</button><span class="muted">Security demo: an unauthorized master changes CV-101's setpoint.</span></div>
   <div class="muted">Tip: stop the main fan (F-301 → Stop fan) and watch methane rise at GM-401 until the CH₄ interlock trips CB-2.</div></div></div>
  <h2 style="margin-top:18px">Devices</h2><div class="grid g3" id="cards">${S.devices.map(card).join("")}</div>`;
  $("#swAuto").onclick = () => scenario(anyAuto ? "auto_off" : "auto_on");
  $("#btnReset").onclick = () => scenario("reset");
  $("#btnRogue").onclick = () => scenario("rogue");
  el.querySelectorAll("[data-go]").forEach(g => { g.style.cursor = "pointer"; g.onclick = () => location.hash = "#/device/" + g.dataset.go; });
  bindCtl(el); drawSparks(el);
}
function card(d) {
  const al = alarmsFor(d.id), cls = sevClass(al) === "crit" ? "crit" : al.length ? "alarm" : "";
  const vals = d.kpis.map(k => { const p = pt(d, k), v = d.values[k], c = sevClass(alarmsFor(d.id, k)); const pct = p.max ? Math.max(0, Math.min(100, v / p.max * 100)) : 0;
    return `<div class="val ${c}"><div class="l">${esc(p.label)}</div><div class="n">${fmtv(v, p)}<small>${esc(p.units || "")}</small></div><div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div></div>`; }).join("");
  return `<div class="card ${cls}"><div class="cardhead"><div class="t"><b>${esc(d.name)}</b><span>${esc(d.role)} · ${esc(d.area)}</span></div><span class="state ${esc(d.state)}">${esc(d.state)}</span></div>
   <div class="vals">${vals}</div><canvas class="spark" data-key="${d.id}:${d.kpis[0]}"></canvas>
   <div class="row">${al.length ? `<span class="sev ${al[0].severity}">${al.length} alarm${al.length > 1 ? "s" : ""}</span><span class="muted">${esc(al[0].label)}</span>` : `<span class="muted ok">No active alarms</span>`}</div>
   <div class="btns">${ctlButtons(d, (d.controls || []).filter(c => c.kind !== "setpoint").slice(0, 4))}<a class="btn small" href="#/device/${d.id}">Details →</a></div></div>`;
}
function drawSparks(el) {
  el.querySelectorAll("canvas.spark").forEach(cv => { const [dev, p] = cv.dataset.key.split(":"); const d = devById(dev);
    drawChart(cv, [{name: p, data: HIST[cv.dataset.key] || []}], {spark: true, secs: 300, limits: limitsFor(d, p)}); });
}
function viewAlarms(el) {
  const rows = S.alarms.map(a => { const st = a.active ? (a.acked ? "ACTIVE · ACKED" : "ACTIVE · UNACKED") : "RETURNED · UNACKED";
    return `<tr class="${a.active ? "active" : "rtn"} ${a.acked ? "" : "unack"}"><td><span class="sev ${a.severity}">${a.severity}</span></td><td>${st}</td><td>${tsLocal(a.raised_at)}</td>
    <td><a href="#/device/${a.device}">${esc(a.device_name)}</a></td><td>${esc(a.label)}</td><td class="tag">${esc(a.rule_id)}</td>
    <td>${a.rule_id === "R-PROC-STATE" || a.rule_id === "R-POLL-ERR" ? `<span class="muted">${a.active ? "in alarm" : "normal"}</span>` : (a.value == null ? "" : esc(a.value) + " " + esc(a.units || "")) + (a.limit != null ? ` <span class="muted">/ ${a.limit}</span>` : "")}</td><td>${a.count}</td>
    <td>${a.acked ? "" : `<button class="btn small" data-ack="${esc(a.key)}">Ack</button>`}</td></tr>`; }).join("");
  const hist = S.events.filter(e => /^R-(PROC|RNG|WR|POLL)/.test(e.rule_id)).slice(0, 40);
  el.innerHTML = `<div class="row"><div><h1>Alarms</h1><p class="sub">ISA-18.2 style: an alarm stays listed until it has returned to normal <i>and</i> been acknowledged.</p></div><span class="spacer"></span><button class="btn" id="ackAll2">Acknowledge all</button></div>
  <div class="panel">${S.alarms.length ? `<table><tr><th>Sev</th><th>State</th><th>Raised</th><th>Device</th><th>Alarm</th><th>Rule</th><th>Value / limit</th><th>#</th><th></th></tr>${rows}</table>` : `<div class="empty">✔ No active or unacknowledged alarms</div>`}</div>
  <h2 style="margin-top:18px">Alarm history</h2><div class="panel">${evTable(hist)}</div>`;
  el.querySelectorAll("[data-ack]").forEach(b => b.onclick = () => ack(b.dataset.ack));
  $("#ackAll2").onclick = () => ack();
}
function evTable(list) {
  if (!list.length) return `<div class="empty">No events yet</div>`;
  return `<table><tr><th>Time</th><th>Sev</th><th>Rule</th><th>Device</th><th>Event</th></tr>${list.map(e => `<tr><td>${tsLocal(e.observed_at)}</td><td><span class="sev ${e.severity}">${e.severity}</span></td><td class="tag">${esc(e.rule_id)}</td><td>${esc(e.dst_name || "")}</td><td>${esc(e.title)}</td></tr>`).join("")}</table>`;
}
function allTrendKeys() { const out = []; S.devices.forEach(d => d.points.filter(p => p.trend).forEach(p => out.push({key: d.id + ":" + p.id, d, p}))); return out; }
function viewTrends(el) {
  const all = allTrendKeys();
  if (!trendSel) trendSel = ["rtu-gas-01:ch4", "plc-dewater-01:sump_level", "plc-vent-01:vibration", "plc-conveyor-01:motor_current", "rtu-sub-01:voltage_kv", "rtu-sub-01:total_kw"];
  el.innerHTML = `<div class="row"><div><h1>Trends</h1><p class="sub">Click points to add or remove charts. Dashed lines are alarm limits. Hover a chart to read values.</p></div><span class="spacer"></span>
   <select id="secs">${[[120, "2 min"], [300, "5 min"], [600, "10 min"], [900, "15 min"]].map(([s, l]) => `<option value="${s}" ${s === trendSecs ? "selected" : ""}>${l}</option>`).join("")}</select></div>
   ${S.devices.map(d => `<div class="chips"><b class="muted">${esc(d.name.split(" ")[0])}</b>${d.points.filter(p => p.trend).map(p => `<span class="chip ${trendSel.includes(d.id + ":" + p.id) ? "on" : ""}" data-k="${d.id}:${p.id}">${esc(p.label)}</span>`).join("")}</div>`).join("")}
   <div class="grid g2" id="charts">${trendSel.map(k => { const x = all.find(a => a.key === k); return x ? chartBox(x.d, x.p) : ""; }).join("")}</div>`;
  el.querySelectorAll(".chip").forEach(c => c.onclick = () => { const k = c.dataset.k; trendSel = trendSel.includes(k) ? trendSel.filter(x => x !== k) : [...trendSel, k]; render(); updateCharts(); });
  $("#secs").onchange = e => { trendSecs = +e.target.value; updateCharts(); };
  updateCharts();
}
function chartBox(d, p) { const v = d.values[p.id];
  return `<div class="chart"><h3>${esc(d.name.split(" ")[0])} · ${esc(p.label)} <span>${fmtv(v, p)} ${esc(p.units || "")}</span></h3><canvas data-key="${d.id}:${p.id}"></canvas></div>`; }
async function updateCharts() {
  const cvs = [...document.querySelectorAll(".chart canvas")]; if (!cvs.length) return;
  await loadHist(cvs.map(c => c.dataset.key), trendSecs).catch(() => {});
  cvs.forEach(cv => { const [dev, pid] = cv.dataset.key.split(":"); const d = devById(dev); const p = pt(d, pid);
    cv._opt = {secs: trendSecs, limits: limitsFor(d, pid), min0: p.units === "%" || p.units === "kW" || p.units === "A"};
    drawChart(cv, [{name: p.label, data: HIST[cv.dataset.key] || []}], cv._opt); attachHover(cv); });
}
function viewDevice(el, id) {
  const d = devById(id); if (!d) { el.innerHTML = `<div class="empty">Unknown device ${esc(id)}</div>`; return; }
  const al = alarmsFor(id);
  const regName = {input: "Input reg (FC4)", discrete: "Discrete in (FC2)", coil: "Coil (FC1/FC5)", holding: "Holding reg (FC3/FC6)"};
  const rows = d.points.map(p => { const a = alarmsFor(id, p.id); const v = d.values[p.id];
    return `<tr><td>${esc(p.label)}</td><td class="tag">${esc(p.id)}</td><td>${regName[p.type]}</td><td class="tag">${p.addr}</td><td class="tag">${p.scale ? "÷10^" + p.scale : "1"}</td>
     <td class="${sevClass(a) === "crit" ? "bad" : a.length ? "warnc" : ""}"><b>${fmtv(v, p)}</b> ${esc(p.units || "")}</td><td>${p.baseline ? p.baseline.join("–") : ""}</td></tr>`; }).join("");
  const sps = (d.controls || []).filter(c => c.kind === "setpoint").map((c, i) => { const p = pt(d, c.point); const v = d.values[c.point] ?? c.min;
    return `<div class="setp"><label>${esc(c.label)}</label><input type="range" min="${c.min}" max="${c.max}" step="${c.step || 1}" value="${v}" data-sp="${c.point}"><input type="number" min="${c.min}" max="${c.max}" step="${c.step || 1}" value="${v}" data-spn="${c.point}"> ${esc(p.units || "")}
     <button class="btn small primary" data-apply="${c.point}" ${S.health.hmi_writes_enabled && d.comm.ok ? "" : "disabled"}>Apply</button><span class="muted">current ${fmtv(d.values[c.point], p)}</span></div>`; }).join("");
  el.innerHTML = `<div class="row"><div><h1>${esc(d.name)} <span class="state ${esc(d.state)}">${esc(d.state)}</span></h1>
   <p class="sub">${esc(d.role)} · ${esc(d.area)} · Modbus TCP <span class="tag">${esc(d.host)}:${d.port}</span> unit ${d.unit}${d.dnp3 ? ` · DNP3 outstation <span class="tag">:${d.dnp3.port}</span> addr ${d.dnp3.outstation}` : ""} · poll ${d.comm.latency_ms ?? "–"} ms</p></div></div>
   <div class="grid g2"><div class="panel"><h2>Operator controls</h2>${(d.controls || []).length ? `<div class="btns">${ctlButtons(d, d.controls || [])}</div>${sps}` : `<div class="muted">This device has no operator controls (monitoring only).</div>`}
     <h2 style="margin-top:14px">Fault injection (simulation)</h2><div class="btns">${ctlButtons(d, d.faults || [], "fault")}</div>
     <div class="toggle" style="margin-top:10px"><button class="sw ${d.values.auto_faults ? "on" : ""}" id="devAuto"></button><span>Automatic fault scenario on this device</span></div></div>
    <div class="panel"><h2>Active alarms</h2>${al.length ? `<table>${al.map(a => `<tr><td><span class="sev ${a.severity}">${a.severity}</span></td><td>${esc(a.label)}</td><td>${tsLocal(a.raised_at)}</td><td>${a.acked ? "acked" : `<button class="btn small" data-ack="${esc(a.key)}">Ack</button>`}</td></tr>`).join("")}</table>` : `<div class="empty">✔ No active alarms</div>`}</div></div>
   <h2 style="margin-top:18px">Trends</h2><div class="grid g2">${d.points.filter(p => p.trend).map(p => chartBox(d, p)).join("")}</div>
   <h2 style="margin-top:18px">Register map &amp; live values</h2><div class="panel"><table><tr><th>Point</th><th>Tag</th><th>Type</th><th>Address</th><th>Scale</th><th>Value</th><th>Baseline</th></tr>${rows}</table></div>`;
  bindCtl(el);
  el.querySelectorAll("[data-ack]").forEach(b => b.onclick = () => ack(b.dataset.ack));
  el.querySelectorAll("[data-sp]").forEach(r => r.oninput = () => { el.querySelector(`[data-spn="${r.dataset.sp}"]`).value = r.value; });
  el.querySelectorAll("[data-spn]").forEach(n => n.oninput = () => { el.querySelector(`[data-sp="${n.dataset.spn}"]`).value = n.value; });
  el.querySelectorAll("[data-apply]").forEach(b => b.onclick = () => { const p = b.dataset.apply; const v = +el.querySelector(`[data-spn="${p}"]`).value;
    doWrite(d.id, p, v, `${d.name.split(" ")[0]}: ${pt(d, p).label} → ${v}`); });
  $("#devAuto").onclick = () => doWrite(d.id, "auto_faults", d.values.auto_faults ? 0 : 1, `${d.name.split(" ")[0]}: auto faults ${d.values.auto_faults ? "OFF" : "ON"}`);
  updateCharts();
}
function viewEvents(el) {
  const cats = {all: () => true, process: e => /^R-PROC|^R-RNG/.test(e.rule_id), operator: e => e.rule_id === "R-HMI-WRITE", security: e => /^R-WR|^R-RNG/.test(e.rule_id), comms: e => /^R-POLL/.test(e.rule_id)};
  api("/api/events?limit=500").then(ev => {
    el.innerHTML = `<div class="row"><div><h1>Events &amp; audit log</h1><p class="sub">Every alarm transition and every operator write, in OTMon's <span class="tag">otmon.finding/v1</span> format (also written to findings.jsonl).</p></div><span class="spacer"></span>
     <select id="evf">${Object.keys(cats).map(k => `<option ${k === evFilter ? "selected" : ""}>${k}</option>`).join("")}</select></div><div class="panel">${evTable(ev.filter(cats[evFilter]).slice(0, 300))}</div>`;
    $("#evf").onchange = e => { evFilter = e.target.value; viewEvents(el); };
  });
}
function viewSystem(el) {
  el.innerHTML = `<h1>System &amp; communications</h1><p class="sub">Monitor uptime ${Math.round(S.health.uptime_s)} s · status <b class="${S.health.status === "ok" ? "ok" : "bad"}">${S.health.status}</b> · <a href="/health" target="_blank">/health</a> · <a href="/api/status" target="_blank">/api/status</a></p>
  <div class="panel"><table><tr><th>Device</th><th>Endpoint</th><th>Link</th><th>Latency</th><th>Polls</th><th>Errors</th><th>Last good poll</th><th>Last error</th></tr>
  ${S.devices.map(d => `<tr><td><a href="#/device/${d.id}">${esc(d.name)}</a></td><td class="tag">${esc(d.host)}:${d.port}</td><td class="${d.comm.ok ? "ok" : "bad"}">${d.comm.ok ? "● online" : "● offline"}</td><td>${d.comm.latency_ms ?? "–"} ms</td><td>${d.comm.polls}</td><td>${d.comm.errors}</td><td>${tsLocal(d.comm.last_ok)}</td><td class="muted">${esc(d.comm.last_error || "")}</td></tr>`).join("")}</table></div>
  <div class="grid g2" style="margin-top:14px"><div class="panel"><h2>How this works</h2><p>Each device is an <b>ot-sim</b> process group (CPU, ZeroMQ message bus, logic, Modbus server; the substation also runs a DNP3 outstation) in its own container. The logic module runs the physics and fault scenarios; the Modbus module exposes them as registers.</p>
   <p>This monitor polls every device <b>read-only</b> (FC1-4) once a second, as OTMon's poller does, and raises alarms on transitions. Operator buttons use a <b>separate</b> write path (FC5/FC6) that only reaches whitelisted points and is audit-logged. Set <span class="tag">OTMON_HMI_WRITES=0</span> for a pure read-only monitor.</p></div>
   <div class="panel"><h2>Alarm rule families</h2><table><tr><td class="tag">R-PROC-HI/HIHI/LO/LOLO</td><td>Analog process limits (2 % deadband)</td></tr><tr><td class="tag">R-PROC-STATE</td><td>Trips, interlocks, breaker open</td></tr>
   <tr><td class="tag">R-RNG-003</td><td>Setpoint outside approved baseline (OTMon)</td></tr><tr><td class="tag">R-WR-001</td><td>Watched setpoint/command changed by someone other than this HMI</td></tr>
   <tr><td class="tag">R-POLL-ERR/OK</td><td>Device communication lost / restored</td></tr><tr><td class="tag">R-HMI-WRITE</td><td>Audit record of each operator write</td></tr></table></div></div>`;
}

/* ---------------- router & loop ---------------- */
function parseRoute() { const h = location.hash.replace(/^#\/?/, "") || "overview"; const [r, ...rest] = h.split("/"); route = r; routeArg = rest.join("/") || null; }
function render() {
  if (!S) return; renderHeader();
  const el = $("#view"), y = window.scrollY;
  // do not re-render while the user is dragging a setpoint slider or typing
  const a = document.activeElement; if (a && (a.tagName === "INPUT" || a.tagName === "SELECT") && el.contains(a)) return;
  ({overview: viewOverview, alarms: viewAlarms, trends: viewTrends, device: e => viewDevice(e, routeArg), events: viewEvents, system: viewSystem}[route] || viewOverview)(el);
  window.scrollTo(0, y);
}
let tick = 0;
async function refresh() {
  try {
    S = await api("/api/status");
    if (route === "overview" && tick % 2 === 0) await loadHist(S.devices.map(d => d.id + ":" + d.kpis[0]), 300).catch(() => {});
    if (route !== "events" || tick % 5 === 0) render();
  } catch (e) { $("#site").textContent = "⚠ cannot reach monitor API – is otmon-mine running?"; }
  tick++;
}
window.addEventListener("hashchange", () => { parseRoute(); window.scrollTo(0, 0); render(); });
window.addEventListener("resize", () => render());
$("#ackAll").onclick = () => ack();
setInterval(() => { $("#clock").textContent = new Date().toLocaleString(); }, 1000);
parseRoute(); refresh(); setInterval(refresh, 1000);
