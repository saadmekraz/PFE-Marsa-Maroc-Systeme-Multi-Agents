// ═══ Marsa Maroc — frontend logic ═══
const API = "";                       // même origine (Flask sert le front)
let JOB = null, DASH = null, charts = {};

// ── authentification ────────────────────────────────────────────────
async function checkAuth() {
  try {
    const d = await (await fetch(`${API}/api/me`)).json();
    if (d.auth) { unlockApp(d.user); } else { showLogin(); }
  } catch { showLogin(); }
}
function showLogin() {
  document.getElementById("loginGate").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
}
function unlockApp(user) {
  document.getElementById("loginGate").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
  const lbl = document.getElementById("userLabel");
  const out = document.getElementById("logoutBtn");
  if (lbl) lbl.textContent = "Connecté : " + (user || "agent");
  if (out) out.classList.remove("hidden");
}
document.getElementById("loginBtn").addEventListener("click", doLogin);
document.getElementById("loginPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
async function doLogin() {
  const username = document.getElementById("loginUser").value;
  const password = document.getElementById("loginPass").value;
  document.getElementById("loginMsg").textContent = "Connexion…";
  const res = await fetch(`${API}/api/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }) });
  const d = await res.json();
  if (d.ok) { document.getElementById("loginMsg").textContent = ""; unlockApp(d.user); }
  else { document.getElementById("loginMsg").textContent = d.error || "Échec de connexion"; }
}
document.getElementById("logoutBtn").addEventListener("click", async () => {
  await fetch(`${API}/api/logout`, { method: "POST" });
  location.reload();
});
checkAuth();


const AGENTS = [
  { n: "01", nom: "Nettoyage",      key: "Nettoyage" },
  { n: "02", nom: "Déduplication",  key: "Déduplication" },
  { n: "03", nom: "Normalisation",  key: "Normalisation" },
  { n: "04", nom: "Classification", key: "Classification" },
  { n: "05", nom: "Enrichissement", key: "Enrichissement" },
];

const $ = (s) => document.querySelector(s);
const fmt = (n) => (n ?? 0).toLocaleString("fr-FR");

// ── build the agent rail ────────────────────────────────────────────
function buildRail() {
  $("#rail").innerHTML = AGENTS.map((a) => `
    <div class="station" id="st-${a.key}">
      <div class="dot">${a.n}</div>
      <div class="font-display text-sm font-medium text-white mb-1">${a.nom}</div>
      <div class="count" id="cnt-${a.key}">—</div>
      <div class="text-[11px] text-steel" id="lbl-${a.key}">en attente</div>
    </div>`).join("");
}
buildRail();

// ── upload ──────────────────────────────────────────────────────────
const fileInput = $("#fileInput");
$("#dropzone").addEventListener("dragover", (e) => { e.preventDefault(); });
$("#dropzone").addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer.files[0]) { fileInput.files = e.dataTransfer.files; handleFile(); }
});
fileInput.addEventListener("change", handleFile);

async function handleFile() {
  const f = fileInput.files[0];
  if (!f) return;
  $("#dropTitle").textContent = f.name;
  $("#dropSub").textContent = "Lecture en cours…";
  const fd = new FormData(); fd.append("file", f);
  const res = await fetch(`${API}/api/upload`, { method: "POST", body: fd });
  const data = await res.json();
  if (data.error) { $("#dropSub").textContent = data.error; return; }
  JOB = data.job_id;
  $("#dropSub").textContent = `${fmt(data.n_articles)} articles détectés`;
  $("#uploadInfo").textContent = `${f.name} · ${fmt(data.n_articles)} articles`;
  $("#runBtn").disabled = false;
  // manifest preview
  $("#manifest").innerHTML = data.preview.map((p) =>
    `<div><span class="text-slate-500">${p.code_sap.slice(-8)}</span> ${escapeHtml((p.description||"").slice(0,40))}</div>`
  ).join("") + `<div class="text-harbor pt-2">+ ${fmt(data.n_articles - data.preview.length)} autres…</div>`;
}

// ── run pipeline + animate rail ─────────────────────────────────────
$("#runBtn").addEventListener("click", async () => {
  if (!JOB) return;
  $("#runBtn").disabled = true;
  $("#railState").textContent = "traitement…";
  AGENTS.forEach((a) => {
    $(`#st-${a.key}`).classList.remove("done");
    $(`#lbl-${a.key}`).textContent = "en attente";
  });
  await fetch(`${API}/api/run/${JOB}`, { method: "POST" });
  poll();
});

async function poll() {
  const res = await fetch(`${API}/api/status/${JOB}`);
  const s = await res.json();
  // mark agents based on progress
  const done = new Set(s.progress);
  let activeSet = false;
  AGENTS.forEach((a) => {
    const st = $(`#st-${a.key}`);
    if (done.has(a.key)) {
      st.classList.add("done"); st.classList.remove("active");
      $(`#lbl-${a.key}`).textContent = "terminé";
    } else if (!activeSet) {
      st.classList.add("active");
      $(`#lbl-${a.key}`).innerHTML = '<span class="pulse text-amber">traitement…</span>';
      activeSet = true;
    }
  });
  if (s.termine) {
    AGENTS.forEach((a) => { $(`#st-${a.key}`).classList.add("done"); $(`#st-${a.key}`).classList.remove("active"); });
    $("#railState").textContent = "terminé";
    loadDashboard();
  } else {
    setTimeout(poll, 700);
  }
}

// ── dashboard ───────────────────────────────────────────────────────
async function loadDashboard() {
  const res = await fetch(`${API}/api/results/${JOB}/dashboard`);
  DASH = await res.json();
  const r = DASH.rapport;

  // fill rail counts
  for (const [k, v] of Object.entries(DASH.agents)) {
    const el = $(`#cnt-${k}`); if (el) el.textContent = fmt(v);
  }

  $("#results").classList.remove("hidden");
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });

  // KPI strip
  $("#kpis").innerHTML = [
    ["Articles", fmt(r.total), ""],
    ["Taux auto", r.taux_auto + "%", r.taux_auto >= 70 ? "harbor" : "amber"],
    ["Qualité moy.", r.score_qualite_moyen + "/100", ""],
    ["Précision classif.", r.precision_classif + "%", "harbor"],
    ["Durée", (r.duree_s ?? "—") + "s", ""],
  ].map(([l, v, c]) => `
    <div class="card">
      <div class="text-xs text-steel uppercase tracking-wide mb-1">${l}</div>
      <div class="font-display text-2xl font-bold ${c ? "text-" + c : "text-white"}">${v}</div>
    </div>`).join("");

  // KPI vs CDC table
  const cdc = [
    ["Taux migration auto", r.taux_auto + "%", "≥ 70%", r.taux_auto >= 70],
    ["Précision classification", r.precision_classif + "%", "≥ 92%", r.precision_classif >= 92],
    ["Articles bloqués", fmt(r.bloques), "minimal", r.bloques < 50],
    ["Score qualité moyen", r.score_qualite_moyen + "/100", "≥ 98", r.score_qualite_moyen >= 98],
  ];
  $("#kpiTable").innerHTML = `<table class="data"><thead><tr>
    <th>Indicateur</th><th>Mesuré</th><th>Cible CDC</th><th>Statut</th></tr></thead><tbody>
    ${cdc.map(([i, m, c, ok]) => `<tr><td>${i}</td><td class="mono">${m}</td><td class="text-steel">${c}</td>
      <td>${ok ? '<span class="badge badge-ready">✓ conforme</span>' : '<span class="badge badge-review">à surveiller</span>'}</td></tr>`).join("")}
    </tbody></table>`;

  drawCharts();
  loadValidation(); loadDuplicates(); loadCodif();
  setupDownloads();
}

function drawCharts() {
  const stat = DASH.statut, fam = DASH.familles;
  const COL = { "CODIFIÉ":"#0d9488", "DOUBLON_FUSIONNÉ":"#3b82f6", VALIDATION_HUMAINE:"#f59e0b", "BLOQUÉ":"#e11d48" };
  if (charts.s) charts.s.destroy();
  charts.s = new Chart($("#chartStatut"), {
    type: "bar",
    data: { labels: Object.keys(stat),
      datasets: [{ data: Object.values(stat),
        backgroundColor: Object.keys(stat).map((k) => COL[k] || "#7a1b3d"),
        borderRadius: 4 }] },
    options: barOpts(true),
  });
  if (charts.f) charts.f.destroy();
  charts.f = new Chart($("#chartFam"), {
    type: "bar",
    data: { labels: Object.keys(fam),
      datasets: [{ data: Object.values(fam), backgroundColor: "#9d2b5a", borderRadius: 4 }] },
    options: barOpts(true),
  });
}
function barOpts(horizontal) {
  return {
    indexAxis: horizontal ? "y" : "x",
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: "rgba(255,255,255,.05)" }, ticks: { color: "#64748b", font: { size: 10 } } },
      y: { grid: { display: false }, ticks: { color: "#cbd5e1", font: { size: 11 } } },
    },
    maintainAspectRatio: false,
  };
}

// ── validation tab ──────────────────────────────────────────────────
async function loadValidation() {
  const d = await (await fetch(`${API}/api/results/${JOB}/validation`)).json();
  $("#validCount").textContent = fmt(d.total);
  $("#validMotifs").innerHTML = Object.entries(d.motifs).map(([m, n]) =>
    `<span class="badge badge-review">${m} · ${fmt(n)}</span>`).join("");
  $("#validTable").innerHTML = tableHtml(
    ["Code", "Description", "Famille IA", "Confiance", "Motif"],
    d.rows.map((r) => [
      `<span class="mono">${r.code_sap.slice(-10)}</span>`,
      escapeHtml(r.description || ""),
      r.famille,
      `<span class="mono">${(r.confiance * 100).toFixed(0)}%</span>`,
      `<span class="text-steel text-xs">${r.motif}</span>`,
    ]));
}

// ── duplicates tab ──────────────────────────────────────────────────
async function loadDuplicates() {
  const d = await (await fetch(`${API}/api/results/${JOB}/duplicates`)).json();
  $("#dedupKpis").innerHTML = [
    ["Paires détectées", fmt(d.paires), "≥ 0.85"],
    ["Fusion auto", fmt(d.auto), "≥ 0.90"],
    ["Clusters uniques", fmt(d.clusters), "après dédup"],
  ].map(([l, v, s]) => `<div class="card"><div class="text-xs text-steel uppercase mb-1">${l}</div>
    <div class="font-display text-2xl font-bold text-white">${v}</div>
    <div class="text-[11px] text-steel mt-1">${s}</div></div>`).join("");
  $("#dedupTable").innerHTML = tableHtml(
    ["Code", "Description", "Doublon détecté", "Maître"],
    d.rows.map((r) => [
      `<span class="mono">${r.code_sap.slice(-10)}</span>`,
      escapeHtml(r.description || ""),
      `<span class="text-xs text-steel mono">${escapeHtml(r.doublon || "")}</span>`,
      r.maitre ? '<span class="badge badge-ready">maître</span>' : '<span class="badge badge-dup">doublon</span>',
    ]));
}

// ── codification → mongodb tab ──────────────────────────────────────
async function loadCodif() {
  const d = await (await fetch(`${API}/api/results/${JOB}/codifies`)).json();
  $("#codifCount").textContent = fmt(d.codifies);
  $("#collName").textContent = d.collection || "articles_codifies";
  $("#codifTable").innerHTML = tableHtml(
    ["Code article", "Description (≤40c)", "Famille", "Marque", "Réf. fab."],
    d.rows.map((r) => [
      `<span class="mono">${r.code.slice(-10)}</span>`,
      escapeHtml(r.description || ""),
      `<span class="text-xs">${r.famille}</span>`,
      `<span class="text-steel">${r.marque || "—"}</span>`,
      `<span class="mono text-xs text-steel">${escapeHtml(r.ref || "—")}</span>`,
    ]));
}

function showMongoMsg(html, ok) {
  const el = $("#mongoMsg");
  el.className = `mb-4 rounded-lg px-4 py-3 text-sm border ${ok
    ? "border-harbor/40 bg-harbor/10 text-harbor" : "border-signal/40 bg-signal/10 text-signal"}`;
  el.innerHTML = html;
  el.classList.remove("hidden");
}

$("#mongoTest").addEventListener("click", async () => {
  const uri = $("#mongoUri").value;
  showMongoMsg("Test de connexion…", true);
  const d = await (await fetch(`${API}/api/mongo/test`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uri }) })).json();
  showMongoMsg(d.message, d.ok);
});

$("#saveMongoBtn").addEventListener("click", async () => {
  if (!JOB) { showMongoMsg("Lancez d'abord le pipeline.", false); return; }
  const uri = $("#mongoUri").value;
  $("#saveMongoBtn").textContent = "Enregistrement…";
  const d = await (await fetch(`${API}/api/save/${JOB}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uri }) })).json();
  $("#saveMongoBtn").textContent = "⬆ Enregistrer dans MongoDB";
  if (d.ok) {
    showMongoMsg(`✓ ${fmt(d.inseres)} insérés · ${fmt(d.modifies)} mis à jour · `
      + `collection <span class="mono">${d.collection}</span> (${fmt(d.total)} au total)`, true);
  } else {
    showMongoMsg("Échec : " + (d.message || "MongoDB injoignable"), false);
  }
});

function setupDownloads() {
  $("#dlValid").href = `${API}/api/results/${JOB}/download/validation`;
  $("#dlCodif").href = `${API}/api/results/${JOB}/download/codifies`;
}

// ── tabs ────────────────────────────────────────────────────────────
document.querySelectorAll(".tab-btn").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((x) => x.classList.add("hidden"));
    b.classList.add("active");
    $(`#tab-${b.dataset.tab}`).classList.remove("hidden");
  });
});
document.querySelector(".tab-btn").classList.add("active");

// ── helpers ─────────────────────────────────────────────────────────
function tableHtml(headers, rows) {
  return `<div class="scrollbox"><table class="data"><thead><tr>${
    headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")
  }</tbody></table></div>`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}