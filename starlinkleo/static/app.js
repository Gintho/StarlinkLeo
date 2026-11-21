/* StarlinkLeo — front */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const mbps = (bps) => (bps == null ? null : bps / 1e6);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const C = { dl: "#2563eb", ul: "#9333ea", lat: "#0891b2", drop: "#dc2626", obs: "#d97706", pow: "#16a34a" };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

Chart.defaults.color = css("--muted");
Chart.defaults.borderColor = css("--line");
Chart.defaults.font.family = "system-ui, sans-serif";
Chart.defaults.animation = false;
Chart.defaults.elements.point.radius = 0;
Chart.defaults.elements.line.borderWidth = 1.5;
Chart.defaults.plugins.legend.labels.boxWidth = 12;

const timeLabels = (ts) => ts.map((t) => new Date(t * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }));
const dayLabels = (ts) => ts.map((t) => new Date(t * 1000).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }));

function lineChart(id, datasets, yTitle, extra = {}) {
  return new Chart($(id), {
    type: "line",
    data: { labels: [], datasets: datasets.map((d) => ({ fill: false, tension: 0.2, ...d })) },
    options: {
      responsive: true, maintainAspectRatio: true, aspectRatio: 2.4, interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { beginAtZero: true, title: { display: !!yTitle, text: yTitle } }, ...extra.scales },
      plugins: { legend: { position: "bottom" } },
    },
  });
}

// ---------------------------------------------------------------- onglets
$$("#tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
    onTab(b.dataset.tab);
  })
);
function onTab(tab) {
  if (tab === "history") loadHistory();
  if (tab === "sky") loadSky();
  if (tab === "latency") loadBB();
  if (tab === "optimize") loadOptimize();
}

// ---------------------------------------------------------------- tableau de bord
const live = { ts: [], dl: [], ul: [], lat: [], drop: [] };
const LIVE_MAX = 300;
const cLiveT = lineChart("#c-live-tput", [
  { label: "Download Mb/s", borderColor: C.dl, data: [] },
  { label: "Upload Mb/s", borderColor: C.ul, data: [] },
], "Mb/s");
const cLiveL = lineChart("#c-live-lat", [
  { label: "Latence ms", borderColor: C.lat, data: [], yAxisID: "y" },
  { label: "Perte %", borderColor: C.drop, data: [], yAxisID: "y1" },
], "ms", { scales: { y1: { position: "right", beginAtZero: true, max: 100, grid: { drawOnChartArea: false } } } });

let controlsEnabled = true;

async function refreshStatus() {
  let r;
  try { r = await api("/api/status"); } catch (e) { setConn(false, e.message); return; }
  $("#mock-badge").classList.toggle("hidden", !r.mock);
  controlsEnabled = r.controls_enabled;
  const s = r.status;
  if (!s) { setConn(false, r.error || "Pas de données"); return; }
  setConn(!r.error, r.error ? "Erreur : " + r.error : `${s.state} · mis à jour il y a ${fmt(r.age_s, 0)} s`);
  $("#k-dl").textContent = fmt(mbps(s.downlink_throughput_bps));
  $("#k-ul").textContent = fmt(mbps(s.uplink_throughput_bps));
  $("#k-lat").textContent = fmt(s.pop_ping_latency_ms, 0);
  $("#k-drop").textContent = fmt((s.pop_ping_drop_rate || 0) * 100);
  $("#k-obs").textContent = fmt((s.fraction_obstructed || 0) * 100, 2);
  $("#k-pow").textContent = fmt(s.power_w, 0);

  if (live.ts.at(-1) !== r.ts) {
    live.ts.push(r.ts); live.dl.push(mbps(s.downlink_throughput_bps)); live.ul.push(mbps(s.uplink_throughput_bps));
    live.lat.push(s.pop_ping_latency_ms); live.drop.push((s.pop_ping_drop_rate || 0) * 100);
    for (const k in live) if (live[k].length > LIVE_MAX) live[k].shift();
    const labels = live.ts.map((t) => new Date(t * 1000).toLocaleTimeString("fr-FR"));
    cLiveT.data.labels = labels; cLiveT.data.datasets[0].data = live.dl; cLiveT.data.datasets[1].data = live.ul; cLiveT.update();
    cLiveL.data.labels = labels; cLiveL.data.datasets[0].data = live.lat; cLiveL.data.datasets[1].data = live.drop; cLiveL.update();
  }

  const up = s.uptime ? `${Math.floor(s.uptime / 86400)} j ${Math.floor((s.uptime % 86400) / 3600)} h` : "–";
  $("#dish-info").innerHTML = [
    ["État", s.state], ["Matériel", s.hardware_version], ["Firmware", s.software_version],
    ["Uptime", up], ["SNR au-dessus du bruit", s.is_snr_above_noise_floor ? "oui" : "non"],
    ["GPS", s.gps_ready ? `OK (${s.gps_sats ?? "?"} sat.)` : "non prêt"],
    ["Mise à jour", s.software_update_state || "–"],
    ["Alertes", (s.active_alert_labels || []).join(", ") || "aucune"],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${esc(v ?? "–")}</td></tr>`).join("");
  $("#orient").innerHTML = [
    ["Azimut", fmt(s.direction_azimuth) + "°"], ["Élévation", fmt(s.direction_elevation) + "°"],
    ["Obstrué actuellement", s.currently_obstructed ? "oui" : "non"],
    ["Durée moy. obstruction", fmt(s.obstruction_duration) + " s"],
    ["Intervalle moy. obstruction", fmt(s.obstruction_interval, 0) + " s"],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join("");
  lastStatus = s;
}
let lastStatus = null;
function setConn(ok, txt) { const c = $("#conn"); c.className = "conn " + (ok ? "ok" : "bad"); c.textContent = txt; }

async function refreshFindings() {
  try {
    const { findings } = await api("/api/diagnostics");
    $("#findings").innerHTML = findings.map((f) =>
      `<li class="${f.severity}"><div class="t">${esc(f.title)}</div>${f.detail ? `<div class="d">${esc(f.detail)}</div>` : ""}${f.action ? `<div class="a">${esc(f.action)}</div>` : ""}</li>`
    ).join("");
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- historique
let range = "1h";
$$("#ranges button").forEach((b) => b.addEventListener("click", () => {
  $$("#ranges button").forEach((x) => x.classList.toggle("active", x === b));
  range = b.dataset.range; loadHistory();
}));
const cHT = lineChart("#c-h-tput", [
  { label: "Download moy.", borderColor: C.dl, data: [] }, { label: "Download max", borderColor: C.dl, borderDash: [4, 3], data: [] },
  { label: "Upload moy.", borderColor: C.ul, data: [] }, { label: "Upload max", borderColor: C.ul, borderDash: [4, 3], data: [] },
], "Mb/s");
const cHL = lineChart("#c-h-lat", [
  { label: "Latence moy.", borderColor: C.lat, data: [] }, { label: "Latence max", borderColor: C.lat, borderDash: [4, 3], data: [] },
], "ms");
const cHD = lineChart("#c-h-drop", [
  { label: "Perte %", borderColor: C.drop, data: [] }, { label: "Obstrué %", borderColor: C.obs, data: [] },
], "%");
const cHP = lineChart("#c-h-pow", [{ label: "Puissance W", borderColor: C.pow, data: [] }], "W");
const cHourly = new Chart($("#c-hourly"), {
  type: "bar",
  data: { labels: [], datasets: [
    { label: "Download max Mb/s", backgroundColor: C.dl, data: [], yAxisID: "y" },
    { label: "Latence moy. ms", type: "line", borderColor: C.lat, data: [], yAxisID: "y1" },
  ] },
  options: { aspectRatio: 2.4, scales: { y: { beginAtZero: true }, y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false } } }, plugins: { legend: { position: "bottom" } } },
});

async function loadHistory() {
  const [{ series: s }, stats] = await Promise.all([api("/api/history?range=" + range), api("/api/stats")]);
  const ts = s.ts || [];
  const labels = ["15m", "1h", "6h"].includes(range) ? timeLabels(ts) : dayLabels(ts);
  const set = (ch, arrs) => { ch.data.labels = labels; arrs.forEach((a, i) => (ch.data.datasets[i].data = a)); ch.update(); };
  const m = (a) => (a || []).map(mbps);
  set(cHT, [m(s.dl_bps), m(s.dl_max_bps), m(s.ul_bps), m(s.ul_max_bps)]);
  set(cHL, [s.latency_ms || [], s.latency_max_ms || []]);
  set(cHD, [(s.drop_rate || []).map((x) => x * 100), (s.obstructed || []).map((x) => x * 100)]);
  set(cHP, [s.power_w || []]);
  const h = stats.hourly_7d;
  cHourly.data.labels = h.map((x) => x.h + "h");
  cHourly.data.datasets[0].data = h.map((x) => mbps(x.dl_max));
  cHourly.data.datasets[1].data = h.map((x) => x.lat);
  cHourly.update();
}

// ---------------------------------------------------------------- carte du ciel
const cWedges = new Chart($("#c-wedges"), {
  type: "polarArea",
  data: { labels: ["N", "NNE", "ENE", "E", "ESE", "SSE", "S", "SSO", "OSO", "O", "ONO", "NNO"], datasets: [{ data: [], backgroundColor: "rgba(239,68,68,.6)" }] },
  options: { aspectRatio: 1.6, plugins: { legend: { display: false } }, scales: { r: { beginAtZero: true, ticks: { callback: (v) => v + "%" } } } },
});

async function loadSky() {
  if (lastStatus?.wedges_fraction_obstructed) {
    cWedges.data.datasets[0].data = lastStatus.wedges_fraction_obstructed.map((x) => (x || 0) * 100);
    cWedges.update();
  }
  const cv = $("#sky"), ctx = cv.getContext("2d");
  let rows;
  try { ({ rows } = await api("/api/obstruction-map")); } catch (e) {
    ctx.clearRect(0, 0, cv.width, cv.height); ctx.fillStyle = css("--muted"); ctx.fillText("Carte indisponible : " + e.message, 20, 240); return;
  }
  const n = rows.length, m = rows[0]?.length || 1, px = cv.width / Math.max(n, m);
  ctx.clearRect(0, 0, cv.width, cv.height);
  for (let y = 0; y < n; y++) for (let x = 0; x < m; x++) {
    const v = rows[y][x];
    if (v < 0) continue;
    ctx.fillStyle = v >= 0.5 ? `rgba(34,197,94,${0.35 + v * 0.65})` : `rgba(239,68,68,${1 - v * 0.6})`;
    ctx.fillRect(x * px, y * px, Math.ceil(px), Math.ceil(px));
  }
  // repères
  const c = cv.width / 2;
  ctx.strokeStyle = css("--muted"); ctx.globalAlpha = 0.5;
  [c - 2, c * 0.66, c * 0.33].forEach((r) => { ctx.beginPath(); ctx.arc(c, c, r, 0, 2 * Math.PI); ctx.stroke(); });
  ctx.beginPath(); ctx.moveTo(c, 0); ctx.lineTo(c, cv.height); ctx.moveTo(0, c); ctx.lineTo(cv.width, c); ctx.stroke();
  ctx.globalAlpha = 1; ctx.fillStyle = css("--fg"); ctx.font = "bold 16px system-ui"; ctx.textAlign = "center";
  ctx.fillText("N", c, 18); ctx.fillText("S", c, cv.height - 6); ctx.fillText("E", cv.width - 12, c + 5); ctx.fillText("O", 12, c + 5);
}
$("#sky-refresh").addEventListener("click", loadSky);

// ---------------------------------------------------------------- bufferbloat
const cBB = new Chart($("#c-bb"), {
  type: "line",
  data: { labels: [], datasets: [
    { label: "Latence repos", borderColor: C.lat, data: [], pointRadius: 3 },
    { label: "Latence en download", borderColor: C.dl, data: [], pointRadius: 3 },
    { label: "Latence en upload", borderColor: C.ul, data: [], pointRadius: 3 },
  ] },
  options: { aspectRatio: 3, scales: { y: { beginAtZero: true, title: { display: true, text: "ms (médiane)" } } }, plugins: { legend: { position: "bottom" } } },
});
const gcls = (g) => "g-" + (g === "A+" ? "Ap" : g === "?" ? "x" : g);

function renderBB(r) {
  const box = (p, label) => `<div class="phase-box"><div>${label}</div><b>${fmt(p.median_ms, 0)} ms</b>
    ${p.increase_ms != null ? `<div>+${fmt(p.increase_ms, 0)} ms · note ${p.grade}</div>` : ""}
    ${p.mbps != null ? `<div>${fmt(p.mbps)} Mb/s</div>` : ""}<div class="hint">p90 ${fmt(p.p90_ms, 0)} ms</div></div>`;
  $("#bb-result").innerHTML = `<div class="grade ${gcls(r.grade)}">${r.grade}</div>` +
    box(r.idle, "Au repos") + box(r.download, "Download chargé") + box(r.upload, "Upload chargé");
}

async function loadBB() {
  const { current, history } = await api("/api/bufferbloat");
  if (current?.status === "running") { $("#bb-phase").textContent = "Phase : " + current.phase + "…"; $("#bb-run").disabled = true; setTimeout(loadBB, 1000); }
  else { $("#bb-run").disabled = false; $("#bb-phase").textContent = current?.status === "error" ? "Erreur : " + current.error : ""; }
  const h = [...history].reverse();
  if (history[0] && current?.status !== "running") renderBB(history[0]);
  cBB.data.labels = dayLabels(h.map((x) => x.ts));
  cBB.data.datasets[0].data = h.map((x) => x.idle.median_ms);
  cBB.data.datasets[1].data = h.map((x) => x.download.median_ms);
  cBB.data.datasets[2].data = h.map((x) => x.upload.median_ms);
  cBB.update();
  $("#bb-table").innerHTML = "<tr><th>Date</th><th>Note</th><th>Down</th><th>Up</th><th>+ms down</th><th>+ms up</th></tr>" +
    history.map((x) => `<tr><td>${dayLabels([x.ts])[0]}</td><td>${x.grade}</td><td>${fmt(x.download.mbps)}</td><td>${fmt(x.upload.mbps)}</td><td>${fmt(x.download.increase_ms, 0)}</td><td>${fmt(x.upload.increase_ms, 0)}</td></tr>`).join("");
}
$("#bb-run").addEventListener("click", async () => {
  if (!confirm("Lancer le test ? Il sature la connexion ~30 s et consomme plusieurs centaines de Mo.")) return;
  try { await api("/api/bufferbloat", { method: "POST" }); } catch (e) { alert(e.message); }
  loadBB();
});

// ---------------------------------------------------------------- optimisation
async function loadOptimize() {
  const dev = encodeURIComponent($("#wan-dev").value.trim() || "eth1");
  const r = await api("/api/optimize?wan_dev=" + dev);
  const k = r.rates, f = (x) => fmt(x / 1000, 0);
  $("#rates").innerHTML = `<tr><th></th><th>min</th><th>base</th><th>max</th><th>SQM fixe</th></tr>
    <tr><td>Download (Mb/s)</td><td>${f(k.min_dl)}</td><td>${f(k.base_dl)}</td><td>${f(k.max_dl)}</td><td>${f(k.static_dl)}</td></tr>
    <tr><td>Upload (Mb/s)</td><td>${f(k.min_ul)}</td><td>${f(k.base_ul)}</td><td>${f(k.max_ul)}</td><td>${f(k.static_ul)}</td></tr>`;
  $("#rates-src").textContent = k.source === "mesures"
    ? `Basé sur ${k.samples} test(s) et les pointes de débit des 7 derniers jours.`
    : "⚠️ Aucun test de débit : valeurs Starlink typiques par défaut. Lancez quelques tests (à différentes heures) pour affiner.";
  $("#f-install").textContent = r.files.install;
  $("#f-sqm").textContent = r.files.sqm;
  $("#f-car").textContent = r.files.cake_autorate;
  $("#f-sqm-static").textContent = r.files.sqm_static;
  $("#dl-install").href = "/api/optimize/download/install_openwrt.sh?wan_dev=" + dev;
  $("#dl-sqm").href = "/api/optimize/download/sqm?wan_dev=" + dev;
  $("#dl-car").href = "/api/optimize/download/config.primary.sh?wan_dev=" + dev;
}
$("#opt-refresh").addEventListener("click", loadOptimize);

// ---------------------------------------------------------------- contrôle
const LABELS = { reboot: "redémarrer la parabole", stow: "ranger la parabole (coupe Internet)", unstow: "déployer la parabole" };
$$(".actions button").forEach((b) => b.addEventListener("click", async () => {
  if (!controlsEnabled) { $("#ctl-msg").textContent = "Contrôles désactivés côté serveur."; return; }
  if (!confirm(`Voulez-vous vraiment ${LABELS[b.dataset.action]} ?`)) return;
  try { await api("/api/control/" + b.dataset.action, { method: "POST" }); $("#ctl-msg").textContent = "Commande envoyée : " + b.dataset.action; }
  catch (e) { $("#ctl-msg").textContent = "Échec : " + e.message; }
}));

// ---------------------------------------------------------------- boucle
refreshStatus(); refreshFindings();
setInterval(refreshStatus, 2000);
setInterval(refreshFindings, 15000);
