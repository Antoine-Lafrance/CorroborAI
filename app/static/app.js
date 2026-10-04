"use strict";

/* ------------------------------------------------------------------ constants */
const VERDICTS = ["ANOMALIE", "AMBIGU", "ECART_JUSTIFIE", "CONFORME"];
const LABEL = { CONFORME: "Conforme", ECART_JUSTIFIE: "Écart justifié", AMBIGU: "Ambigu", ANOMALIE: "Anomalie" };
const ICON = {
  CONFORME: '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  ECART_JUSTIFIE: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M8 7.5v4M8 5h0"/></svg>',
  AMBIGU: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M6.2 6.3a1.9 1.9 0 0 1 3.6.7c0 1.3-1.8 1.6-1.8 2.7M8 11.8h0"/></svg>',
  ANOMALIE: '<svg viewBox="0 0 16 16"><path d="M8 2.2l6.2 11H1.8z"/><path d="M8 6.5v3M8 11.6h0"/></svg>',
};
const LAYER = { DETERMINISTE: "Règle métier", IA: "IA", EXPERT: "Expert" };
const REQUIRED = ["Employe_Source_Anonymise_VF.xlsx", "Employe_Destination_Anonymise_VF.xlsx",
  "détail_du_poste.xlsx", "Motif de la situation d'emploi.xlsx"];
const ARROW = '<svg viewBox="0 0 20 20"><path d="M4 10h12m-4-4l4 4-4 4"/></svg>';
const CHECK = '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg>';

const state = { ds: "default", data: null, rules: null, view: "overview", rule: null,
  filter: { verdict: "ALL", layer: "ALL", field: "ALL", q: "", limit: 100 }, files: [] };
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");

/* ------------------------------------------------------------------ helpers */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v) => (v === null || v === undefined || v === "" ? '<span class="muted">∅</span>' : esc(v));
const pct = (x) => `${Math.round((x ?? 0) * 100)} %`;
const chip = (v) => `<span class="chip v-${v}">${ICON[v] || ""}${LABEL[v] || esc(v)}</span>`;
const layerTag = (c) => `<span class="tag l-${c}">${LAYER[c] || esc(c)}</span>`;
const prio = (p) => p ? `<span class="prio num ${p >= 80 ? "p-high" : p >= 50 ? "p-mid" : ""}">${p}</span>` : '<span class="muted">–</span>';
const conf = (c) => `<span class="conf"><span class="conf-track"><span class="conf-fill" style="width:${Math.round(c * 100)}%"></span></span>${pct(c)}</span>`;
const who = (r) => `<span class="mono">${esc(r.Matricule)}</span>${r.TypeAffectation ? ` <span class="muted">· ${esc(r.TypeAffectation)}</span>` : ""}`;

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return res.json();
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}
const withDs = (p) => `${p}${p.includes("?") ? "&" : "?"}ds=${encodeURIComponent(state.ds)}`;
const rowById = (id) => state.data.rows.find((r) => r.Id === id);

/* tooltips for any element with data-tip */
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest("[data-tip]");
  const tip = $("#tooltip");
  if (!el) { tip.classList.remove("show"); return; }
  tip.innerHTML = el.dataset.tip;
  tip.classList.add("show");
  const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${e.clientY + 16}px`;
});

/* ------------------------------------------------------------------ loading */
async function load() {
  main.innerHTML = '<div class="loading">Corroboration en cours : règles métier puis couche IA…</div>';
  state.data = await api(withDs("/api/results"));
  $("#export-xlsx").href = withDs("/api/export.xlsx");
  $("#export-csv").href = withDs("/api/export.csv");
  const todo = state.data.counts.ANOMALIE + state.data.counts.AMBIGU;
  $("#nav-todo").textContent = todo || "";
  render();
}
async function loadDatasets(select) {
  const list = await api("/api/datasets");
  const sel = $("#dataset");
  sel.innerHTML = list.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
  if (select) state.ds = select;
  sel.value = state.ds;
}

/* ------------------------------------------------------------------ routing */
function route() {
  const v = (location.hash || "#overview").slice(1);
  state.view = ["overview", "investigate", "all", "demo", "rules", "data"].includes(v) ? v : "overview";
  document.querySelectorAll(".sidenav a").forEach((a) => a.classList.toggle("active", a.dataset.view === state.view));
  closeDrawer();
  if (state.data) render();
}
function render() {
  ({ overview, investigate, all: allRows, demo, rules, data: dataView })[state.view]();
  main.focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ views: overview */
function overview() {
  const d = state.data;
  const c = d.counts, det = d.deterministic;
  const total = d.rows.length;
  const todo = c.ANOMALIE + c.AMBIGU;
  const iaResolved = d.layers.IA;
  const iaToJust = d.flow.AMBIGU.ECART_JUSTIFIE, iaToBad = d.flow.AMBIGU.ANOMALIE, iaLeft = d.flow.AMBIGU.AMBIGU;
  const li = (v, n) => `<li><span><span class="dot bg-${v}"></span>${LABEL[v]}</span><b class="num">${n}</b></li>`;

  main.innerHTML = `
    <h1>Vue d'ensemble</h1>
    <p class="lede">${total} vérifications champ par champ sur ${d.employees} employés (${esc(d.dataset)}).
      Les règles métier tranchent d'abord ; la couche IA analyse ce qu'elles ne peuvent pas trancher ;
      le rapport final ne garde que ce qui mérite une investigation.</p>

    <div class="kpis">
      <div class="card kpi k-bad"><div class="label">${ICON.ANOMALIE} À investiguer</div>
        <div class="value num">${todo}</div><div class="sub">${c.ANOMALIE} anomalies · ${c.AMBIGU} ambigus</div></div>
      <div class="card kpi k-just"><div class="label">${ICON.ECART_JUSTIFIE} Écarts justifiés</div>
        <div class="value num">${c.ECART_JUSTIFIE}</div><div class="sub">expliqués automatiquement</div></div>
      <div class="card kpi k-ok"><div class="label">${ICON.CONFORME} Conformes</div>
        <div class="value num">${c.CONFORME}</div><div class="sub">${pct(c.CONFORME / total)} des vérifications</div></div>
      <div class="card kpi k-ia"><div class="label"><svg viewBox="0 0 16 16"><path d="M8 1.5l1.6 4.2 4.4.3-3.4 2.8 1.1 4.3L8 10.7l-3.7 2.4 1.1-4.3L2 6l4.4-.3z"/></svg> Résolus par l'IA</div>
        <div class="value num">${iaResolved}</div><div class="sub">sur ${det.AMBIGU} cas ambigus pour les règles</div></div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Du brut au rapport final</h2><span class="muted">Chaque couche est traçable dans le chemin de décision</span></div>
      <div class="pipeline">
        <div class="card stage"><div class="step">1 · Comparaison brute</div><div class="title">Valeurs identiques ?</div>
          <div class="big num">${total}</div><div class="muted">vérifications de champs mappés, après normalisation (dates, vides, accents, encodage)</div></div>
        <div class="arrow">${ARROW}</div>
        <div class="card stage"><div class="step">2 · Règles métier</div><div class="title">Arbres de décision du mapping</div>
          <ul>${li("CONFORME", det.CONFORME)}${li("ECART_JUSTIFIE", det.ECART_JUSTIFIE)}${li("ANOMALIE", det.ANOMALIE)}${li("AMBIGU", det.AMBIGU)}</ul></div>
        <div class="arrow">${ARROW}</div>
        <div class="card stage"><div class="step">3 · Couche IA</div><div class="title">${det.AMBIGU} cas ambigus analysés</div>
          <ul>${li("ECART_JUSTIFIE", iaToJust)}${li("ANOMALIE", iaToBad)}${li("AMBIGU", iaLeft)}</ul>
          <div class="muted" style="margin-top:8px;font-size:12px">Règles apprises + signatures d'écart, 100 % local</div></div>
        <div class="arrow">${ARROW}</div>
        <div class="card stage final"><div class="step">Rapport final</div><div class="title">À investiguer</div>
          <div class="big num">${todo}</div><div style="opacity:.75">triés par priorité ; ${c.ECART_JUSTIFIE + c.CONFORME} lignes écartées avec justification</div></div>
      </div>
    </div>

    <div class="section grid-2">
      <div class="card">
        <div class="card-pad section-head" style="margin:0"><h2>Priorités d'investigation</h2><a class="btn btn-sm" href="#investigate">Tout voir</a></div>
        ${rowsTable(d.rows.filter((r) => r.Verdict === "ANOMALIE" || r.Verdict === "AMBIGU").slice(0, 8), "compact")}
      </div>
      <div class="card card-pad">
        <div class="section-head"><h2>Verdicts par champ</h2></div>
        <div class="legend" style="margin-bottom:12px">${VERDICTS.map((v) => `<span><span class="dot bg-${v}"></span>${LABEL[v]}</span>`).join("")}</div>
        ${fieldBars(d.byField)}
      </div>
    </div>`;
  bindRows();
  main.querySelectorAll(".bar-row").forEach((el) => el.addEventListener("click", () => {
    state.filter = { ...state.filter, field: el.dataset.field, verdict: "ALL", layer: "ALL", q: "" };
    location.hash = "#all";
  }));
}

function fieldBars(byField) {
  const sorted = [...byField].sort((a, b) => (b.ANOMALIE - a.ANOMALIE) || (b.AMBIGU - a.AMBIGU)
    || (b.ECART_JUSTIFIE - a.ECART_JUSTIFIE) || a.field.localeCompare(b.field));
  const max = Math.max(...sorted.map((f) => VERDICTS.reduce((s, v) => s + f[v], 0)));
  return `<div class="bars">${sorted.map((f) => {
    const tot = VERDICTS.reduce((s, v) => s + f[v], 0);
    const segs = VERDICTS.filter((v) => f[v]).map((v) =>
      `<span class="bar-seg bg-${v}" style="flex:${f[v]}" data-tip="<b>${esc(f.field)}</b><br>${LABEL[v]} : ${f[v]}"></span>`).join("");
    return `<div class="bar-row" data-field="${esc(f.field)}" title="Filtrer sur ${esc(f.field)}">
      <span class="name">${esc(f.field)}</span>
      <span class="bar-track" style="width:${(tot / max) * 100}%">${segs}</span>
      <span class="total num">${tot}</span></div>`;
  }).join("")}</div>`;
}

/* ------------------------------------------------------------------ tables */
function rowsTable(rows, mode) {
  if (!rows.length) return '<div class="empty">Aucune vérification ne correspond.</div>';
  const compact = mode === "compact";
  const head = compact
    ? "<th>Prio.</th><th>Verdict</th><th>Champ</th><th>Employé</th>"
    : "<th>Prio.</th><th>Verdict</th><th>Couche</th><th>Champ</th><th>Employé</th><th>Attendu</th><th>Cible</th><th>Justification</th>";
  const body = rows.map((r) => compact
    ? `<tr data-id="${esc(r.Id)}"><td>${prio(r.Priorite)}</td><td>${chip(r.Verdict)}</td><td class="mono">${esc(r.ChampCible)}</td><td>${who(r)}</td></tr>`
    : `<tr data-id="${esc(r.Id)}"><td>${prio(r.Priorite)}</td><td>${chip(r.Verdict)}</td><td>${layerTag(r.Couche)}</td>
        <td class="mono">${esc(r.ChampCible)}</td><td>${who(r)}</td>
        <td><span class="val">${fmt(r.ValeurAttendue)}</span></td><td><span class="val">${fmt(r.ValeurCible)}</span></td>
        <td class="just">${esc(r.Justification)}</td></tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function bindRows() {
  main.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openRow(tr.dataset.id)));
}

/* ------------------------------------------------------------------ views: investigate */
function investigate() {
  const rows = state.data.rows.filter((r) => r.Verdict === "ANOMALIE" || r.Verdict === "AMBIGU")
    .sort((a, b) => b.Priorite - a.Priorite);
  main.innerHTML = `
    <h1>À investiguer</h1>
    <p class="lede">Le rapport final : uniquement les écarts qu'aucune règle métier ni l'analyse IA ne justifie.
      Priorité = impact métier du champ × confiance du verdict. Cliquez une ligne pour voir les données et le chemin de décision.</p>
    <div class="card">${rows.length ? rowsTable(rows) : '<div class="empty">Rien à investiguer : tous les écarts sont justifiés.</div>'}
      <div class="table-foot">${rows.length} ligne(s) · ${state.data.counts.ECART_JUSTIFIE} écarts justifiés et ${state.data.counts.CONFORME} conformes exclus</div></div>`;
  bindRows();
}

/* ------------------------------------------------------------------ views: all */
function filtered() {
  const f = state.filter, q = f.q.trim().toLowerCase();
  return state.data.rows.filter((r) => (f.verdict === "ALL" || r.Verdict === f.verdict)
    && (f.layer === "ALL" || r.Couche === f.layer)
    && (f.field === "ALL" || r.ChampCible === f.field)
    && (!q || [r.Matricule, r.Nom, r.ValeurAttendue, r.ValeurCible, r.Justification, r.Regle]
      .some((v) => String(v ?? "").toLowerCase().includes(q))));
}
function allRows() {
  const f = state.filter, rows = state.data.rows;
  const fields = [...new Set(rows.map((r) => r.ChampCible))].sort();
  const cnt = (v) => rows.filter((r) => (v === "ALL" || r.Verdict === v)).length;
  main.innerHTML = `
    <h1>Toutes les vérifications</h1>
    <p class="lede">Chaque champ mappé de chaque affectation, avec son verdict, la règle appliquée et sa justification.</p>
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Verdict">
        ${["ALL", ...VERDICTS].map((v) => `<button data-v="${v}" aria-pressed="${f.verdict === v}">${v === "ALL" ? "Tous" : `<span class="dot bg-${v}"></span>${LABEL[v]}`} <span class="muted num">${cnt(v)}</span></button>`).join("")}
      </div>
      <select id="f-layer" aria-label="Couche"><option value="ALL">Toutes les couches</option>
        ${Object.entries(LAYER).map(([k, l]) => `<option value="${k}" ${f.layer === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select id="f-field" aria-label="Champ"><option value="ALL">Tous les champs</option>
        ${fields.map((x) => `<option ${f.field === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
      <input type="search" id="f-q" placeholder="Matricule, valeur, règle…" value="${esc(f.q)}" aria-label="Recherche">
    </div>
    <div class="card" id="all-table"></div>`;
  const draw = () => {
    const list = filtered();
    $("#all-table").innerHTML = rowsTable(list.slice(0, state.filter.limit))
      + `<div class="table-foot">${Math.min(list.length, state.filter.limit)} / ${list.length} ligne(s)
         ${list.length > state.filter.limit ? ' · <button class="btn btn-sm" id="more">Afficher plus</button>' : ""}</div>`;
    bindRows();
    const more = $("#more");
    if (more) more.onclick = () => { state.filter.limit += 200; draw(); };
  };
  main.querySelectorAll(".seg button").forEach((b) => b.onclick = () => {
    state.filter.verdict = b.dataset.v; state.filter.limit = 100;
    main.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    draw();
  });
  $("#f-layer").onchange = (e) => { state.filter.layer = e.target.value; draw(); };
  $("#f-field").onchange = (e) => { state.filter.field = e.target.value; draw(); };
  $("#f-q").oninput = (e) => { state.filter.q = e.target.value; draw(); };
  draw();
}

/* ------------------------------------------------------------------ decision path */
function parsePath(chemin) {
  return String(chemin || "").split(" | ").map((s) => {
    const m = s.match(/^\[([^\]]+)\]\s*(.*)$/);
    if (!m) return { id: "", q: s, a: "" };
    const [, id, rest] = m;
    if (id.startsWith("IA") || id === "EXPERT") return { id, q: rest, a: "", kind: id === "EXPERT" ? "expert" : "ia" };
    const i = rest.indexOf(" → ");
    return i < 0 ? { id, q: rest, a: "" } : { id, q: rest.slice(0, i), a: rest.slice(i + 3), kind: rest.slice(0, i).endsWith("?") ? "q" : "" };
  });
}
function pathHtml(r) {
  const steps = parsePath(r.Chemin);
  return `<ol class="path">${steps.map((s) => `<li class="${s.kind || ""}"><span class="node"></span>
      <div class="nid">${esc(s.id)}</div>
      <div class="qtext">${esc(s.q)}${s.a ? ` <span class="ans">${esc(s.a)}</span>` : ""}</div></li>`).join("")}
    <li class="leaf"><span class="node"></span><div class="nid">VERDICT · ${esc(r.Regle)}</div><div class="qtext">${chip(r.Verdict)}</div></li></ol>`;
}
function compareHtml(r) {
  const differs = r.ValeurAttendue !== r.ValeurCible;
  const raw = (String(r.Chemin || "").match(/→ raw = ([^|]*?)(?: \||$)/) || [])[1];
  const rawBox = raw && raw.trim() !== String(r.ValeurAttendue) ? `<div style="grid-column:1/-1"><div class="k">Valeur brute dans la source (avant règle métier)</div><div class="v">${fmt(raw.trim())}</div></div>` : "";
  return `<div class="compare">${rawBox}
    <div><div class="k">Attendu selon la règle (Système A – RH)</div><div class="v">${fmt(r.ValeurAttendue)}</div></div>
    <div class="${differs && r.Verdict === "ANOMALIE" ? "diff" : ""}"><div class="k">Trouvé dans la cible (Système B – Temps)</div><div class="v">${fmt(r.ValeurCible)}</div></div></div>`;
}

/* ------------------------------------------------------------------ drawer */
async function openRow(id) {
  const r = rowById(id);
  if (!r) return;
  const body = $("#drawer-body");
  const sameSig = r.Signature ? state.data.rows.filter((x) => x.ChampCible === r.ChampCible && x.Signature === r.Signature).length : 0;
  body.innerHTML = `
    <div class="d-title">${chip(r.Verdict)}${layerTag(r.Couche)}${r.MethodeIA ? `<span class="tag">${esc(r.MethodeIA)}</span>` : ""}</div>
    <h2 class="d-title" style="margin-top:8px"><span>${esc(r.ChampCible)}</span></h2>
    <div class="muted">Employé ${who(r)} · poste ${esc(r.CodePoste || "–")} · emploi ${esc(r.CodeEmploi || "–")} · source : ${esc(r.ChampsSource)}</div>
    <dl class="d-meta">
      <div><dt>Règle</dt><dd class="mono">${esc(r.Regle)}</dd></div>
      <div><dt>Confiance</dt><dd>${pct(r.Confiance)}</dd></div>
      <div><dt>Priorité</dt><dd>${r.Priorite || "–"}</dd></div>
      <div><dt>Impact</dt><dd>${esc(r.Impact)}</dd></div>
    </dl>
    ${compareHtml(r)}
    <h3>Pourquoi ce verdict</h3>
    <div class="why v-${r.Verdict}">${esc(r.Justification)}
      ${r.DescriptionRegle ? `<div class="muted" style="margin-top:6px;font-size:12.5px">Règle ${esc(r.Regle)} : ${esc(r.DescriptionRegle)}</div>` : ""}
      ${r.VerdictDeterministe !== r.Verdict ? `<div class="muted" style="margin-top:6px;font-size:12.5px">Verdict des règles seules : ${LABEL[r.VerdictDeterministe]}</div>` : ""}
      ${sameSig > 1 && r.Signature !== "=" ? `<div class="muted" style="margin-top:6px;font-size:12.5px">Forme d'écart (signature « ${esc(r.Signature)} ») partagée par ${sameSig} lignes de ce champ.</div>` : ""}
    </div>
    <h3>Chemin de décision</h3>
    ${pathHtml(r)}
    <h3>Données ayant servi à la décision</h3>
    <div id="raw" class="muted">Chargement…</div>
    <h3 style="margin-top:20px">Corriger le verdict (expert)</h3>
    <form class="form-grid card card-pad" id="ov-form">
      <div class="form-row">${VERDICTS.map((v) => `<label class="radio"><input type="radio" name="verdict" value="${v}" ${v === r.Verdict ? "checked" : ""}>${LABEL[v]}</label>`).join("")}</div>
      <div class="form-row">
        <label class="radio"><input type="radio" name="portee" value="ligne" checked>Cette ligne seulement</label>
        ${r.Signature ? `<label class="radio"><input type="radio" name="portee" value="motif">Toutes les lignes « ${esc(r.ChampCible)} » de même forme (${sameSig})</label>` : ""}
      </div>
      <textarea name="commentaire" placeholder="Raison de la correction (conservée dans le rapport)"></textarea>
      <div class="form-row"><button class="btn btn-primary" type="submit">Enregistrer la décision</button>
        <span class="muted" style="font-size:12px">Une décision « par forme » devient une règle appliquée aux prochaines corroborations.</span></div>
    </form>`;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  body.scrollTop = 0;

  $("#ov-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = { id: r.Id, champ: r.ChampCible, signature: r.Signature || null, portee: fd.get("portee"),
      verdict: fd.get("verdict"), commentaire: fd.get("commentaire") };
    try {
      await api("/api/override", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      await load();
      toast("Décision experte enregistrée et appliquée");
      openRow(r.Id);
    } catch (err) { toast(err.message); }
  };

  try {
    const raw = await api(withDs(`/api/row?id=${encodeURIComponent(id)}`));
    const kv = (obj, hl) => `<div class="kv">${Object.entries(obj).sort(([a], [b]) => hl.includes(b) - hl.includes(a)).map(([k, v]) =>
      `<div class="k ${hl.includes(k) ? "hl" : ""}">${esc(k)}</div><div class="${hl.includes(k) ? "hl" : ""}">${fmt(v)}</div>`).join("")}</div>`;
    $("#raw").outerHTML = Object.keys(raw.source).length
      ? `<details class="raw" open><summary>Système A – RH (source) · colonnes utilisées surlignées</summary>${kv(raw.source, raw.sourceUsed)}</details>
         <details class="raw"><summary>Système B – Temps (cible)</summary>${kv(raw.target, [raw.targetField])}</details>`
      : '<p class="muted">Aucune paire source ↔ cible : l\'affectation n\'existe que d\'un côté.</p>';
  } catch (err) { $("#raw").textContent = err.message; }
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
}
$("#drawer-close").onclick = closeDrawer;
$("#scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

/* ------------------------------------------------------------------ views: demo */
function demo() {
  const cases = [
    ["conforme", "Cas 1", "Un cas conforme", "La cible contient exactement la valeur exigée par la règle du mapping."],
    ["ecart_justifie", "Cas 2", "Un écart justifié automatiquement", "La cible diffère de la valeur brute de la source, mais une validation complémentaire (table des motifs) l'explique."],
    ["anomalie", "Cas 3", "Une vraie anomalie détectée", "La cible contredit la règle : c'est une erreur de données à investiguer."],
    ["ia", "Bonus", "Un cas ambigu tranché par l'IA", "Aucune règle documentée ne tranche ; la couche IA apprend la règle réellement appliquée et la vérifie sur toute la population."],
  ];
  main.innerHTML = `
    <h1>Démonstration</h1>
    <p class="lede">Les trois types de cas exigés par le défi, tirés des données réelles, avec la règle appliquée,
      la justification et le chemin complet qui a mené au verdict.</p>
    <div class="demo-grid">${cases.map(([k, n, title, text]) => {
      const r = rowById(state.data.demo[k]);
      if (!r) return "";
      return `<article class="card demo v-${r.Verdict} ${k === "ia" ? "ia" : ""}">
        <div class="demo-top"><div class="n">${n}</div><h2>${title}</h2><p class="muted" style="margin:0">${text}</p></div>
        <div class="demo-body">
          <div class="form-row" style="margin-bottom:10px">${chip(r.Verdict)}${layerTag(r.Couche)}<span class="mono">${esc(r.ChampCible)}</span><span class="muted">· ${who(r)}</span></div>
          ${compareHtml(r)}
          <div class="why v-${r.Verdict}">${esc(r.Justification)}</div>
          ${pathHtml(r)}
        </div>
        <div class="demo-foot"><span class="muted">Règle ${esc(r.Regle)} · confiance ${pct(r.Confiance)}</span>
          <button class="btn btn-sm" data-open="${esc(r.Id)}">Voir les données</button></div>
      </article>`;
    }).join("")}</div>`;
  main.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openRow(b.dataset.open));
}

/* ------------------------------------------------------------------ views: rules */
async function rules() {
  if (!state.rules) {
    main.innerHTML = '<div class="loading">Chargement des règles…</div>';
    state.rules = await api("/api/rules");
  }
  const learned = Object.fromEntries(state.data.learned.map((l) => [l.Champ, l]));
  const list = state.rules;
  if (!state.rule) state.rule = (list.find((r) => learned[r.field]) || list[0]).field;
  const mini = (c) => VERDICTS.filter((v) => c[v]).map((v) => `<span class="bg-${v}" style="flex:${c[v]}"></span>`).join("");
  const item = (r) => `<button data-f="${esc(r.field)}" aria-current="${r.field === state.rule}">
      <span class="fname">${esc(r.field)}</span><span class="mini">${mini(r.counts)}</span></button>`;
  const ai = list.filter((r) => learned[r.field]), rest = list.filter((r) => !learned[r.field]);
  main.innerHTML = `
    <h1>Règles &amp; IA</h1>
    <p class="lede">Les règles du mapping sont codées en arbres de décision (couche déterministe, traçable nœud par nœud).
      Pour les champs qu'elles ne tranchent pas, l'IA apprend la règle réellement appliquée par la cible et en fournit les preuves.</p>
    <div class="rules-layout">
      <div class="card rule-list">
        ${ai.length ? `<div class="group">Analysés par l'IA</div>${ai.map(item).join("")}` : ""}
        <div class="group">Règles documentées</div>${rest.map(item).join("")}
      </div>
      <div id="rule-detail"></div>
    </div>`;
  main.querySelectorAll(".rule-list button").forEach((b) => b.onclick = () => { state.rule = b.dataset.f; rules(); });
  ruleDetail(list.find((r) => r.field === state.rule), learned[state.rule]);
}

async function ruleDetail(r, learned) {
  const el = $("#rule-detail");
  el.innerHTML = `
    <div class="card card-pad">
      <div class="form-row" style="justify-content:space-between"><h2 class="mono" style="font-size:17px">${esc(r.field)}</h2>
        <div class="form-row">${VERDICTS.filter((v) => r.counts[v]).map((v) => `${chip(v)}<b class="num">${r.counts[v]}</b>`).join(" ")}</div></div>
      <p class="muted" style="margin-top:4px">Mapping : « ${esc(r.description)} » · source : <span class="mono">${esc(r.sources)}</span></p>
      <h3 style="margin-top:14px">Règle documentée ${esc(r.rule)}</h3>
      <p>${esc(r.catalog)}</p>
      <details ${learned ? "" : "open"}><summary style="cursor:pointer;font-weight:600;margin-bottom:8px">Arbre de décision appliqué</summary>
        <pre class="tree">${esc(r.tree)}</pre></details>
    </div>
    <div class="card card-pad section" id="learn-box"><h3>Règle apprise par l'IA</h3><div class="muted">${r.field === "(affectation)" ? "Non applicable à l'appariement." : "Apprentissage en cours (recherche de règles, stabilité leave-one-out)…"}</div></div>`;
  if (r.field === "(affectation)") return;
  try {
    const p = await api(withDs(`/api/field/${encodeURIComponent(r.field)}`));
    if (state.rule !== r.field) return;
    const stab = p.stability.find((s) => s["règle apprise"] === p.rule);
    const maxScore = Math.max(...p.alternatives.map((a) => a.score), 0.0001);
    const minScore = Math.min(...p.alternatives.map((a) => a.score), 0);
    const ev = p.evidence;
    const cols = Object.keys(ev[0] || {}).filter((k) => !["verdict moteur"].includes(k));
    $("#learn-box").innerHTML = `
      <h3>Règle apprise par l'IA (S1b, sans voir la règle documentée)</h3>
      <div class="learned">${p.reliable ? "" : "Rejetée (couverture insuffisante) : "}${esc(p.rule)}</div>
      ${p.reliable ? "" : '<p class="muted" style="font-size:12.5px;margin-top:6px">Aucune règle du langage ne reproduit assez de lignes : l\'IA juge alors ce champ par signatures d\'écart (systémique ou isolé).</p>'}
      ${learned ? `<div class="callout" style="margin-top:10px">${learned["Lignes résolues par l'IA"]} ligne(s) ambiguë(s) tranchée(s) par l'IA sur ce champ.
        Signatures d'écart : <span class="mono">${esc(learned["Signatures d'écart"])}</span></div>` : ""}
      <div class="stat-row">
        <div class="stat"><div class="k">Couverture</div><div class="v">${pct(p.coverage)}</div></div>
        <div class="stat"><div class="k">Stabilité leave-one-out</div><div class="v">${stab ? pct(stab.part) : "–"}</div></div>
        <div class="stat"><div class="k">Sur données pseudonymisées</div><div class="v">${pct(p.privacy["pseudonymisé"])}</div></div>
        <div class="stat"><div class="k">Fiable (≥ ${pct(p.tau)})</div><div class="v">${p.reliable ? "Oui" : "Non"}</div></div>
      </div>
      <h3 style="margin-top:18px">Pourquoi cette règle plutôt qu'une autre</h3>
      <p class="muted" style="font-size:12.5px">Score = couverture − ${p.lambda} × complexité. Hypothèses concurrentes :</p>
      ${p.alternatives.map((a) => `<div class="alt-row ${a["règle"] === p.rule ? "chosen" : ""}">
          <span class="r" title="${esc(a["règle"])}">${esc(a["règle"])}</span>
          <span><span class="alt-bar" style="display:block;width:${Math.max(4, ((a.score - minScore) / (maxScore - minScore || 1)) * 100)}%"
            data-tip="couverture ${pct(a.couverture)} · complexité ${a["complexité"]} · score ${a.score.toFixed(3)}"></span></span>
          <span class="num muted">${pct(a.couverture)}</span></div>`).join("")}
      <h3 style="margin-top:18px">Vérification ligne par ligne</h3>
      <div class="table-wrap" style="max-height:340px;border:1px solid var(--line);border-radius:8px">
        <table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
        <tbody>${ev.map((row) => `<tr style="cursor:default">${cols.map((c) => `<td class="${c === "reproduit" ? "" : "mono"}" style="font-size:12px">${
          c === "reproduit" ? (row[c] ? `<span class="chip v-CONFORME">${CHECK}oui</span>` : '<span class="chip v-ANOMALIE">non</span>') : fmt(row[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>
      </div>`;
  } catch (err) { $("#learn-box").innerHTML = `<h3>Règle apprise par l'IA</h3><p class="muted">${esc(err.message)}</p>`; }
}

/* ------------------------------------------------------------------ views: data */
async function dataView() {
  const ov = state.data.overrides;
  main.innerHTML = `
    <h1>Données &amp; décisions</h1>
    <p class="lede">Chargez de nouvelles extractions pour relancer la corroboration. Les fichiers d'origine ne sont jamais modifiés.</p>
    <div class="grid-2">
      <div class="card card-pad">
        <h2 style="margin-bottom:12px">Charger des extractions</h2>
        <label class="drop" id="drop"><input type="file" id="file-input" multiple accept=".xlsx" hidden>
          <div style="font-weight:600">Déposez les 4 fichiers .xlsx ici</div><div class="muted">ou cliquez pour parcourir</div>
          <ul class="files">${REQUIRED.map((f) => `<li><span class="st ${state.files.some((x) => x.name === f) ? "ok" : ""}">${state.files.some((x) => x.name === f) ? CHECK : ""}</span><span class="mono">${esc(f)}</span></li>`).join("")}</ul>
        </label>
        <div class="form-row" style="margin-top:12px"><button class="btn btn-primary" id="upload" ${REQUIRED.every((f) => state.files.some((x) => x.name === f)) ? "" : "disabled"}>Lancer la corroboration</button>
          <span class="muted" style="font-size:12px">Mapping.xlsx n'est pas requis : ses règles sont codées dans le moteur.</span></div>
      </div>
      <div class="card card-pad">
        <h2 style="margin-bottom:12px">Confidentialité</h2>
        <ul class="privacy">
          <li>${CHECK}<span>Serveur limité à <span class="mono">localhost</span> ; l'IA (recherche de règles, signatures) tourne en local : aucune donnée ne quitte le poste.</span></li>
          <li>${CHECK}<span>Fichiers sources ouverts en lecture seule ; les extractions chargées vont dans un dossier temporaire.</span></li>
          <li>${CHECK}<span>Le module LLM optionnel (désactivé) n'enverrait que 6 lignes pseudonymisées des champs ambigus — voir README.</span></li>
        </ul>
        <h2 style="margin:18px 0 10px">Exporter le rapport</h2>
        <div class="form-row"><a class="btn btn-primary" href="${withDs("/api/export.xlsx")}">Rapport Excel</a><a class="btn" href="${withDs("/api/export.csv")}">CSV complet</a></div>
        <p class="muted" style="font-size:12.5px;margin-top:8px">Onglets : Résumé, À investiguer (par priorité), Écarts justifiés, Conformes, Détail complet, Règles apprises, Catalogue des règles, Décisions expertes.</p>
      </div>
    </div>
    <div class="card section">
      <div class="card-pad section-head" style="margin:0"><h2>Décisions expertes</h2><span class="muted">${ov.length} décision(s) · appliquées à chaque corroboration</span></div>
      ${ov.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Portée</th><th>Champ</th><th>Ligne / forme</th><th>Verdict imposé</th><th>Commentaire</th><th></th></tr></thead><tbody>
        ${ov.map((o, i) => `<tr style="cursor:default"><td class="num">${esc(o.date.replace("T", " "))}</td><td>${o.portee === "ligne" ? "Ligne" : "Forme d'écart"}</td>
          <td class="mono">${esc(o.champ)}</td><td class="mono" style="font-size:12px">${esc(o.portee === "ligne" ? o.id : o.signature)}</td>
          <td>${chip(o.verdict)}</td><td>${esc(o.commentaire)}</td><td><button class="btn btn-sm" data-del="${i}">Retirer</button></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">Aucune décision. Ouvrez une vérification et utilisez « Corriger le verdict ».</div>'}
    </div>
    <div class="card card-pad section" id="eval"><h2>Évaluation des méthodes d'apprentissage</h2><p class="muted">Chargement…</p></div>`;

  const input = $("#file-input"), drop = $("#drop");
  const take = (files) => {
    const byName = Object.fromEntries(state.files.map((f) => [f.name, f]));
    [...files].forEach((f) => { byName[f.name] = f; });
    state.files = Object.values(byName);
    dataView();
  };
  input.onchange = () => take(input.files);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); take(e.dataTransfer.files); };
  $("#upload").onclick = async () => {
    const fd = new FormData();
    state.files.forEach((f) => fd.append("files", f));
    $("#upload").disabled = true;
    $("#upload").textContent = "Corroboration…";
    try {
      const ds = await api("/api/upload", { method: "POST", body: fd });
      state.files = [];
      await loadDatasets(ds.id);
      state.rules = null;
      location.hash = "#overview";
      await load();
      toast(`${ds.name} corroborée`);
    } catch (err) { toast(err.message); dataView(); }
  };
  main.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    await api(`/api/override/${b.dataset.del}`, { method: "DELETE" });
    await load();
    toast("Décision retirée");
  });

  try {
    const ev = await api("/api/evaluation");
    if (state.view !== "data") return;
    const box = $("#eval");
    if (!ev.available) { box.innerHTML = '<h2>Évaluation des méthodes</h2><p class="muted">Lancez <code>python -m corroborai learn</code> pour produire le banc d\'essai.</p>'; return; }
    const short = (m) => m.replace(" (LOO)", "").replace("DSL + conditions", "recherche + conditions").replace("DSL", "recherche");
    box.innerHTML = `<h2 style="margin-bottom:4px">Évaluation des méthodes d'apprentissage</h2>
      <p class="muted" style="font-size:12.5px">A : règles documentées retrouvées sans les voir (sur ${ev.nDocumented}). B : détection de 2 erreurs injectées par champ × ${ev.seeds} graines.</p>
      <div class="table-wrap"><table><thead><tr><th>Méthode</th><th>Règles retrouvées</th><th>Précision</th><th>Rappel</th><th>F1</th></tr></thead><tbody>
      ${ev.prf.map((m) => { const f = ev.found.find((x) => x["méthode"] === m["méthode"]);
        return `<tr style="cursor:default"><td>${esc(short(m["méthode"]))}</td><td class="num">${f ? f["règles retrouvées"] : "–"}</td>
          <td class="num">${m["précision"] != null ? m["précision"].toFixed(2) : "–"}</td><td class="num">${m.rappel != null ? m.rappel.toFixed(2) : "–"}</td>
          <td class="num"><b>${m.F1 != null ? m.F1.toFixed(2) : "–"}</b></td></tr>`; }).join("")}
      </tbody></table></div>
      <p class="muted" style="font-size:12.5px;margin-top:8px">S4 (signatures) s'appuie sur la valeur attendue de la règle documentée : c'est une borne haute. La couche IA de production combine S1b puis S4.</p>`;
  } catch (_) { /* optional */ }
}

/* ------------------------------------------------------------------ boot */
window.addEventListener("hashchange", route);
$("#dataset").onchange = async (e) => { state.ds = e.target.value; state.rules = null; await load(); };
(async () => {
  try {
    await loadDatasets();
    route();
    await load();
  } catch (err) {
    main.innerHTML = `<div class="empty">Erreur : ${esc(err.message)}</div>`;
  }
})();
