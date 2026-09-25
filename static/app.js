const $ = (s) => document.querySelector(s);
const drop = $("#drop"), fileInput = $("#file");

drop.addEventListener("click", () => fileInput.click());
["dragover", "dragenter"].forEach(e =>
  drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(e =>
  drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", ev => { if (ev.dataTransfer.files[0]) upload(ev.dataTransfer.files[0]); });
fileInput.addEventListener("change", () => { if (fileInput.files[0]) upload(fileInput.files[0]); });

const STATUS = {
  a_confirmer: ["À confirmer", "warn"],
  testee: ["Testée", "ok"],
  validee_client: ["Validée client", "ok"],
};

async function upload(file) {
  $("#results").innerHTML = `<div class="empty"><span class="spin"></span> Analyse en cours…</div>`;
  $("#fileMeta").classList.add("hidden");
  const fd = new FormData();
  fd.append("file", file);
  // chemin : champ manuel sinon chemin relatif (dossier déposé) sinon nom
  const relpath = $("#relpath").value.trim() || file.webkitRelativePath || file.name;
  fd.append("relpath", relpath);
  const r = await fetch("/analyze", { method: "POST", body: fd });
  const data = await r.json();
  if (data.error) { $("#results").innerHTML = `<div class="empty">${data.error}</div>`; return; }
  window.lastIncoming = data.incoming;
  window.lastClientFile = file;
  renderMeta(data);
  renderResults(data);
  enableChat();
  askAI("");                       // synthèse automatique
}

function renderMeta(d) {
  const m = $("#fileMeta");
  const inc = d.incoming;
  const head = [];
  if (inc.platform) head.push(`<span class="chip hl">${esc(inc.platform)}${inc.platform_confirmed ? " ✓" : ""}</span>`);
  if (inc.manufacturer) head.push(`<span class="chip hl">${esc(inc.manufacturer)}</span>`);
  const ids = (inc.typed_candidates || []).slice(0, 6)
    .map(c => `<span class="chip" title="${esc(c.type)} · ${esc(c.family)} · confiance ${Math.round(c.confidence*100)}%">${esc(c.value)}</span>`)
    .join("") || '<span class="muted">aucun identifiant détecté</span>';
  const nm = inc.name_meta || {};
  const nmBits = [nm.brand, nm.vehicle, nm.solution_type].filter(Boolean);
  const nameLine = nmBits.length
    ? `<br>D'après le nom : ${nmBits.map(x => `<span class="chip soft">${esc(x)}</span>`).join(" ")}` : "";
  const hdr = inc.header_len
    ? `<br>Header programmeur : <span class="chip soft">${inc.header_tool || "outil"} · ${inc.header_len} o retiré(s) · corps ${(inc.body_size || 0).toLocaleString("fr")} o</span>`
    : "";
  m.innerHTML = `Fichier <b>${esc(d.filename)}</b> · ${inc.size.toLocaleString("fr")} octets ·
    sha256 ${esc((inc.sha256 || "").slice(0, 12))}…${hdr}<br>
    ${head.length ? "ECU : " + head.join(" ") + "<br>" : ""}
    Identifiants : ${ids}${nameLine}`;
  m.classList.remove("hidden");
}

function typeAtomsOf(m) {
  if (m && m.type_atoms && m.type_atoms.length) return m.type_atoms;
  return String((m && m.solution_type) || "").split(/\s*\+\s*/).map(s => s.trim()).filter(Boolean);
}

function renderResults(d) {
  window.lastMatches = d.matches;
  window.lastDbSize = d.db_size;
  window.matchTypeFilter = null;
  paintMatches();
}

function paintMatches() {
  const box = $("#results");
  const all = window.lastMatches || [];
  if (!all.length) {
    box.innerHTML = `<div class="empty">Aucune solution similaire dans la base (${window.lastDbSize || 0} entrées).<br>
      Nouveau dossier à traiter.</div>
      <div class="saverow"><button id="saveBtn">+ Enregistrer comme solution</button>
        <button id="dosFromSearch" class="ghost">Ouvrir un dossier client</button></div>`;
    $("#saveBtn").onclick = openModal;
    $("#dosFromSearch").onclick = () => openDossierFromSearch();
    return;
  }
  const incoming = window.lastIncoming || {};
  const want = incoming.wanted_types || (incoming.name_meta && incoming.name_meta.solution_type
    ? typeAtomsOf({ solution_type: incoming.name_meta.solution_type }) : []);
  const atoms = [];
  for (const m of all) for (const a of typeAtomsOf(m)) if (!atoms.includes(a)) atoms.push(a);
  const filter = window.matchTypeFilter;
  const list = filter ? all.filter(m => typeAtomsOf(m).includes(filter)) : all;
  const chips = atoms.map(a =>
    `<button type="button" class="chip ${filter === a ? "hl" : (want.includes(a) ? "soft" : "")}" data-atom="${esc(a)}">${esc(a)}</button>`
  ).join(" ");
  const asked = want.length
    ? `<span class="muted small">D'après le nom : ${want.map(esc).join(", ")}</span>` : "";
  const combinable = list.filter(m => m.exact || m.same_stock || m.calibration_exact);
  const comboBar = combinable.length > 1
    ? `<div class="combo-bar">
        <button type="button" id="comboAll" class="ghost sm">Tout cocher (même stock)</button>
        <button type="button" id="comboPatch" class="patchbtn">⚙ Combiner et auto-patch (${combinable.length})</button>
        <span class="muted small">Stage 1 + FAP + E85… un seul BIN, conflits signalés</span>
      </div>` : "";
  box.innerHTML =
    `<div class="typechips">${chips}${filter ? ` <button type="button" class="chip" data-atom="">toutes</button>` : ""} ${asked}</div>` +
    comboBar +
    list.map((m, i) => {
    const [lbl, cls] = STATUS[m.tested_status] || [m.tested_status, ""];
    const pct = Math.round(m.score * 100);
    const atomsL = typeAtomsOf(m).map(a => `<span class="chip soft">${esc(a)}</span>`).join(" ");
    const stock = (m.exact || m.same_stock)
      ? `<span class="okmark">même stock</span>` : "";
    return `<div class="card ${i === 0 && !filter ? "best" : ""}">
      <div class="bar"><i style="height:${pct}%"></i></div>
      <div class="score">${pct}<small>SCORE</small></div>
      <div class="card-body">
        <h4>${esc(m.vehicle_label) || "(véhicule non libellé)"} <span class="badge ${cls}">${esc(lbl)}</span> ${stock}</h4>
        <div class="muted">${atomsL || esc(m.solution_type) || "type non précisé"}${m.ecu_version ? " · " + esc(m.ecu_version) : ""}</div>
        <div class="why">${esc(m.reason)}</div>
        ${m.solution_file ? `
        <div class="solfile" title="${esc(m.solution_file)}">📄 ${esc(m.solution_file.split(/[\\/]/).pop())}</div>
        <div class="solactions">
          <label class="patch-chk combo-chk"><input type="checkbox" class="combo-id" value="${m.id}" ${(m.exact || m.same_stock || m.calibration_exact) ? "checked" : ""}> combiner</label>
          <a class="dlbtn" href="/solution/file?id=${m.id}">⬇ Télécharger la solution</a>
          <button class="patchbtn" data-patch="${m.id}">⚙ Auto-patch</button>
          <button class="ghost sm" data-dos="${m.id}">Dossier client</button>
          ${m.exact ? `<span class="okmark">✓ stock identique — prête à livrer</span>`
                    : `<span class="warnmark">⚠ base similaire — à vérifier avant flash</span>`}
        </div>` : ""}
      </div>
    </div>`;
  }).join("") +
  `<div class="saverow"><button id="saveBtn" class="ghost">+ Enregistrer ce fichier comme nouvelle solution</button>
    <button id="dosFromSearch" class="ghost">Ouvrir un dossier client</button></div>`;
  $("#saveBtn").onclick = openModal;
  $("#dosFromSearch").onclick = () => openDossierFromSearch();
  box.querySelectorAll(".patchbtn").forEach(b => {
    b.onclick = () => autoPatchFromMatch(b.dataset.patch);
  });
  box.querySelectorAll("[data-dos]").forEach(b => {
    b.onclick = () => openDossierFromSearch(b.dataset.dos);
  });
  box.querySelectorAll(".typechips [data-atom]").forEach(b => {
    b.onclick = () => {
      const a = b.getAttribute("data-atom") || "";
      window.matchTypeFilter = a || null;
      paintMatches();
    };
  });
  const comboAll = $("#comboAll");
  if (comboAll) comboAll.onclick = () => {
    box.querySelectorAll(".combo-id").forEach(c => { c.checked = true; });
  };
  const comboPatch = $("#comboPatch");
  if (comboPatch) comboPatch.onclick = () => {
    const ids = [...box.querySelectorAll(".combo-id:checked")].map(c => c.value);
    autoPatchFromMatch(ids.join(","));
  };
  const hit = list.find(m => m.stock_sha256 || m.ecu_version);
  if (hit) loadStockHistory(hit.stock_sha256 || "", hit.ecu_version || "", box);
}

async function loadStockHistory(sha, ecu, box) {
  try {
    const d = await (await fetch(`/history?sha=${encodeURIComponent(sha || "")}&ecu=${encodeURIComponent(ecu || "")}`)).json();
    const jobs = d.jobs || [];
    if (!jobs.length) return;
    const html = jobs.slice(0, 8).map(j => {
      const when = j.when ? new Date(j.when * 1000).toLocaleString("fr") : "";
      const dos = j.dossier_id ? ` dossier #${j.dossier_id}` : "";
      return `<li>${esc(when)} — ${esc(j.label || "?")} · ${esc(j.verdict || "")}${dos}</li>`;
    }).join("");
    const bar = document.createElement("div");
    bar.className = "combo-bar";
    bar.innerHTML = `<b>Déjà livré sur ce stock</b><ul class="combo-list">${html}</ul>`;
    box.insertBefore(bar, box.firstChild);
  } catch (e) { /* silencieux */ }
}

/* ---- Assistant ---- */
function enableChat() { $("#q").disabled = false; $("#ask").disabled = false; }
function addMsg(text, who) {
  const c = $("#chat");
  if (c.querySelector(".center")) c.innerHTML = "";
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.textContent = text;
  c.appendChild(div); c.scrollTop = c.scrollHeight;
  return div;
}
async function askAI(question) {
  if (question) addMsg(question, "me");
  const loading = addMsg("…", "ai");
  loading.innerHTML = '<span class="spin"></span>';
  const r = await fetch("/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const d = await r.json();
  loading.textContent = d.text || d.error || "—";
}
$("#ask").onclick = () => { const q = $("#q").value.trim(); if (q) { askAI(q); $("#q").value = ""; } };
$("#q").addEventListener("keydown", e => { if (e.key === "Enter") $("#ask").click(); });

/* ---- Enregistrement ---- */
function openModal() {
  const inc = window.lastIncoming || {};
  const nm = inc.name_meta || {};
  $("#m_label").value = [nm.brand, nm.vehicle].filter(Boolean).join(" ");
  $("#m_ecu").value = inc.best_ecu_version || "";
  $("#m_platform").value = inc.platform || "";
  $("#m_type").value = nm.solution_type || "";
  $("#modal").classList.remove("hidden");
}
$("#m_cancel").onclick = () => $("#modal").classList.add("hidden");
$("#m_save").onclick = async () => {
  const body = {
    vehicle_label: $("#m_label").value, ecu_version: $("#m_ecu").value,
    ecu_platform: $("#m_platform").value,
    solution_type: $("#m_type").value, tested_status: $("#m_status").value,
    notes: $("#m_notes").value,
  };
  const r = await fetch("/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (d.ok) { $("#dbSize").textContent = d.db_size; $("#modal").classList.add("hidden");
    ["m_label","m_ecu","m_platform","m_type","m_notes"].forEach(id => $("#"+id).value = ""); }
};

/* ===================== Onglets ===================== */
function esc(s){return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

function showView(v) {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x.dataset.view === v));
  ["search", "solutions", "import", "viz", "patch", "batch", "dash", "jobs", "inbox", "fs", "clients", "inspect"].forEach(name => {
    const el = document.getElementById("view-" + name);
    if (el) el.classList.toggle("hidden", name !== v);
  });
  if (v === "solutions") loadSolutions();
  if (v === "viz") initViz();
  if (v === "patch") initPatch();
  if (v === "dash") initDash();
  if (v === "jobs" && !window.openingDos) loadJobs();
  if (v === "inbox") loadInbox();
  if (v === "clients") { loadClients(); loadSmtp(); }
  if (v === "fs") { loadFs(); loadFsReglages(); loadFsBackups(false); loadFsSynthese(); loadEquipe(); loadJournal(); }
}
document.querySelectorAll(".tab").forEach(t => { t.onclick = () => showView(t.dataset.view); });

/* Lancer l'auto-patch depuis un résultat de recherche */
async function autoPatchFromMatch(id) {
  if (!window.lastClientFile) {
    alert("Dépose d'abord un fichier client dans l'onglet Recherche.");
    return;
  }
  await initPatch();
  showView("patch");
  const first = String(id).split(",")[0];
  if ($("#patch_sol").querySelector(`option[value="${first}"]`))
    $("#patch_sol").value = first;
  await analyzePatch(window.lastClientFile, String(id));
}

/* ===================== Import depuis l'interface (flux temps réel) ===================== */
const IMP_STATE = { "nouveau": "ok", "ajouté": "ok", "doublon": "", "erreur": "danger" };
let impES = null;
$("#imp_preview").onclick = () => runImport(true);
$("#imp_run").onclick = () => runImport(false);
$("#imp_browse").onclick = async () => {
  const btn = $("#imp_browse");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const r = await fetch("/pick-folder", { method: "POST" });
    const d = await r.json();
    if (d.path) { $("#imp_path").value = d.path; refreshLastScan(); }
    else if (d.error) alert(d.error);
  } catch (e) {
    alert("Sélecteur indisponible. Tape le chemin à la main.");
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
};

async function refreshLastScan() {
  const path = $("#imp_path").value.trim();
  const el = $("#imp_lastscan");
  if (!path) { el.textContent = ""; return; }
  try {
    const r = await fetch("/scan-info", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const d = await r.json();
    el.textContent = d.last_scan ? `— dernier scan : ${d.last_scan}` : "— jamais scanné";
  } catch (e) { el.textContent = ""; }
}
$("#imp_path").addEventListener("change", refreshLastScan);

function showProgress(done, total) {
  $("#imp_progress").classList.remove("hidden");
  const pct = total ? Math.round(done / total * 100) : 0;
  $("#imp_bar").style.width = pct + "%";
  $("#imp_ptext").textContent = total ? `${done} / ${total} dossiers` : "préparation…";
}
function hideProgress() { $("#imp_progress").classList.add("hidden"); }

function liveSummary(c, dry) {
  $("#imp_summary").innerHTML = dry
    ? `<b>${c.new}</b> nouvelle(s) · ${c.dup} déjà en base${c.errors ? " · " + c.errors + " erreur(s)" : ""}`
    : `<b>${c.added}</b> ajoutée(s) · ${c.dup} doublon(s)${c.errors ? " · " + c.errors + " erreur(s)" : ""}`;
}

function appendImportRow(e) {
  const box = $("#imp_results");
  if (box.querySelector(".empty")) box.innerHTML = "";
  const div = document.createElement("div");
  div.className = "sol-row";
  if (e.state === "erreur") {
    div.innerHTML = `<div class="sol-main"><div class="sol-title">
      <span class="badge danger">erreur</span> ${esc(e.folder)}</div>
      <div class="sol-sub">${esc(e.error || "")}</div></div>`;
  } else {
    const cls = IMP_STATE[e.state] || "";
    const sub = [e.type, e.platform, e.ecu].filter(Boolean).map(esc).join(" · ");
    div.innerHTML = `<div class="sol-main">
      <div class="sol-title">${esc(e.vehicle) || esc(e.folder) || "(dossier)"}
        <span class="badge ${cls}">${esc(e.state)}</span></div>
      <div class="sol-sub">${sub || '<span class="muted">—</span>'}</div>
      <div class="sol-notes">📁 ${esc(e.folder)} · original : ${esc(e.original)} → solution : ${esc(e.solution) || "(aucune)"}</div>
    </div>`;
  }
  box.appendChild(div);
}

function runImport(dry) {
  const path = $("#imp_path").value.trim();
  if (!path) { $("#imp_results").innerHTML = `<div class="empty">Indique un dossier.</div>`; return; }
  if (impES) { impES.close(); impES = null; }
  $("#imp_run").classList.add("hidden");
  $("#imp_results").innerHTML = "";
  $("#imp_summary").textContent = (dry ? "Analyse" : "Import") + " en cours…";
  showProgress(0, 0);

  const qs = new URLSearchParams({ path, status: $("#imp_status").value, dry_run: dry,
                                   incremental: $("#imp_incremental").checked });
  impES = new EventSource("/import/stream?" + qs.toString());

  impES.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.event === "error") {
      impES.close(); hideProgress();
      $("#imp_results").innerHTML = `<div class="empty">${esc(ev.error)}</div>`;
      return;
    }
    if (ev.event === "start") { showProgress(0, ev.total); return; }
    if (ev.event === "item") {
      showProgress(ev.index, ev.total);
      appendImportRow(ev.entry);
      liveSummary(ev.counts, dry);
      return;
    }
    if (ev.event === "done") {
      impES.close(); impES = null; hideProgress();
      const c = ev.counts;
      $("#dbSize").textContent = ev.db_size;
      $("#imp_summary").innerHTML = dry
        ? `<b>${c.new}</b> nouvelle(s) à ajouter · ${c.dup} déjà en base${c.errors ? " · " + c.errors + " erreur(s)" : ""}. Vérifie puis « Importer maintenant ».`
        : `✓ <b>${c.added}</b> solution(s) ajoutée(s) · ${c.dup} doublon(s)${c.errors ? " · " + c.errors + " erreur(s)" : ""}`;
      $("#imp_run").classList.toggle("hidden", !(dry && c.new > 0));
      if (!dry) refreshLastScan();
    }
  };
  impES.onerror = () => { if (impES) { impES.close(); impES = null; } hideProgress(); };
}

/* ===================== Traitement par lot ===================== */
let batchES = null;
const BATCH_STATE = { patche: "ok", a_verifier: "warn", incompatible: "danger",
                      sans_solution: "", erreur: "danger" };
const BATCH_LABEL = { patche: "patché", a_verifier: "à vérifier",
                      incompatible: "incompatible", sans_solution: "sans solution", erreur: "erreur" };

$("#batch_browse").onclick = async () => {
  const btn = $("#batch_browse");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await fetch("/pick-folder", { method: "POST" });
    const d = await r.json();
    if (d.path) $("#batch_path").value = d.path;
    else if (d.error) alert(d.error);
  } catch (e) { alert("Sélecteur indisponible. Tape le chemin à la main."); }
  finally { btn.disabled = false; btn.textContent = label; }
};

function showBatchProgress(done, total) {
  $("#batch_progress").classList.remove("hidden");
  const pct = total ? Math.round(done / total * 100) : 0;
  $("#batch_bar").style.width = pct + "%";
  $("#batch_ptext").textContent = total ? `${done} / ${total} fichier(s)` : "préparation…";
}
function hideBatchProgress() { $("#batch_progress").classList.add("hidden"); }

function batchSummary(c) {
  $("#batch_summary").innerHTML =
    `<b>${c.patche}</b> patché(s) · ${c.a_verifier} à vérifier · `
    + `${c.incompatible} incompatible(s) · ${c.sans_solution} sans solution`
    + (c.erreur ? ` · ${c.erreur} erreur(s)` : "");
}

function appendBatchRow(e) {
  const box = $("#batch_results");
  if (box.querySelector(".empty")) box.innerHTML = "";
  const div = document.createElement("div");
  div.className = "sol-row";
  const cls = BATCH_STATE[e.statut] || "";
  const lbl = BATCH_LABEL[e.statut] || e.statut;
  const sub = [e.solution, e.plateforme, e.fabricant].filter(Boolean).map(esc).join(" · ");
  const zonesInfo = e.zones ? ` · ${e.zones} zone(s) · ${e.octets_modifies} octet(s) modifié(s)` : "";
  div.innerHTML = `<div class="sol-main">
    <div class="sol-title">${esc(e.fichier)} <span class="badge ${cls}">${esc(lbl)}</span></div>
    <div class="sol-sub">${sub || '<span class="muted">—</span>'}${zonesInfo}</div>
    <div class="sol-notes">${esc(e.detail || "")}${e.sortie ? " · 📄 " + esc(e.sortie) : ""}</div>
  </div>`;
  box.appendChild(div);
}

$("#batch_run").onclick = () => {
  const path = $("#batch_path").value.trim();
  if (!path) { $("#batch_results").innerHTML = `<div class="empty">Indique un dossier.</div>`; return; }
  if (!confirm("Le traitement va analyser tous les fichiers du dossier et générer "
    + "automatiquement les fichiers patchés pour les correspondances « propres ». "
    + "Les fichiers sources ne sont jamais modifiés. Continuer ?")) return;
  if (batchES) { batchES.close(); batchES = null; }
  $("#batch_run").disabled = true;
  $("#batch_results").innerHTML = "";
  $("#batch_summary").textContent = "Traitement en cours…";
  showBatchProgress(0, 0);

  const qs = new URLSearchParams({ path, min_score: $("#batch_score").value,
                                   apply_clean: $("#batch_apply").checked });
  batchES = new EventSource("/batch/stream?" + qs.toString());

  batchES.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.event === "error") {
      batchES.close(); hideBatchProgress(); $("#batch_run").disabled = false;
      $("#batch_results").innerHTML = `<div class="empty">${esc(ev.error)}</div>`;
      return;
    }
    if (ev.event === "start") { showBatchProgress(0, ev.total); return; }
    if (ev.event === "item") {
      showBatchProgress(ev.index + 1, ev.total);
      appendBatchRow(ev.entry);
      batchSummary(ev.counts);
      return;
    }
    if (ev.event === "done") {
      batchES.close(); batchES = null; hideBatchProgress(); $("#batch_run").disabled = false;
      batchSummary(ev.counts);
      $("#batch_summary").innerHTML += ev.counts.patche
        ? `<br><span class="muted small">Fichiers patchés dans : <code>${esc(ev.out_dir)}</code>. `
          + "Vérifie les checksums avant de flasher.</span>"
        : "";
    }
  };
  batchES.onerror = () => { if (batchES) { batchES.close(); batchES = null; } hideBatchProgress(); $("#batch_run").disabled = false; };
};

/* ===================== Gestion des solutions ===================== */
let solTimer = null;
window.allSolutions = [];
window.solDim = "type";
window.solCat = null;

const SOL_DIM_EMPTY = "(non défini)";

$("#solSearch").addEventListener("input", () => {
  clearTimeout(solTimer);
  solTimer = setTimeout(applyFilters, 150);
});
$("#solDim").addEventListener("change", () => {
  window.solDim = $("#solDim").value;
  window.solCat = null;
  applyFilters();
});
$("#solDim2").addEventListener("change", applyFilters);
$("#solRepair").onclick = async () => {
  $("#solRepair").textContent = "Réparation…"; $("#solRepair").disabled = true;
  try {
    const r = await fetch("/solutions/backfill_originals", { method: "POST" });
    const d = await r.json();
    alert(`Réparation terminée :\n${d.fixed} lien(s) original reconstruit(s)\n`
      + `${d.missing} fichier(s) original introuvable(s) sur le disque\n`
      + `${d.checked} fiche(s) sans original vérifiée(s)`);
  } catch (e) { alert("Erreur : " + e.message); }
  $("#solRepair").textContent = "Réparer les liens"; $("#solRepair").disabled = false;
  vizSolLoaded = false;
};

$("#solRelabel").onclick = async () => {
  if (!confirm("Relire type, plateforme et libellé depuis les chemins (original + solution) ?\nLes champs déjà remplis ne sont pas écrasés, sauf les plateformes-bruit (dMe…) et les libellés visiblement faux.")) return;
  $("#solRelabel").textContent = "Lecture…"; $("#solRelabel").disabled = true;
  try {
    const d = await (await fetch("/solutions/backfill_metadata", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: false }),
    })).json();
    alert(`Fiches relues :\n${d.types} type(s) rempli(s)\n`
      + `${d.platforms} plateforme(s) corrigée(s)\n`
      + `${d.labels} libellé(s) corrigé(s)\n`
      + `${d.manufacturers} fabricant(s) rempli(s)\n`
      + `${d.junk} fiche(s) suspecte(s) (.cache…) étiquetée(s)\n`
      + `${d.unchanged} inchangée(s)`);
  } catch (e) { alert("Erreur : " + e.message); }
  $("#solRelabel").textContent = "Relire les fiches"; $("#solRelabel").disabled = false;
  vizSolLoaded = false;
  loadSolutions();
};

async function rebuildFingerprints(btn) {
  if (!confirm("Lancer la file atelier (archive + empreintes v2 + versions ECU) ?\nOn reprend tout seul si ça s'arrête. Sans BIN sur le disque : pointer CARTOS d'abord.")) return;
  await runAtelierSync(btn);
}
$("#solRebuildFp").onclick = () => rebuildFingerprints($("#solRebuildFp"));
$("#solBackup").onclick = async () => {
  $("#solBackup").textContent = "Sauvegarde…"; $("#solBackup").disabled = true;
  try {
    const r = await fetch("/backup", { method: "POST" });
    const d = await r.json();
    if (d.ok) alert(`Base sauvegardée (${d.count} fiche(s)) :\n${d.path}`);
    else alert("Rien à sauvegarder (base vide).");
  } catch (e) { alert("Erreur : " + e.message); }
  $("#solBackup").textContent = "Sauvegarder"; $("#solBackup").disabled = false;
};

function renderAtelierStatus(s) {
  if (!s) return;
  const el = $("#dashAtelierStats");
  if (!el) return;
  el.innerHTML =
    `<b>${s.fp_v2}</b> / ${s.total} empreintes v2 · `
    + `<b>${s.archived}</b> archivée(s) · `
    + `<b>${s.ecu}</b> versions ECU · `
    + `<b>${s.pending}</b> en attente (BIN présents) · `
    + `${s.junk} suspect(s) · ${s.dup_groups} groupe(s) doublon · `
    + `${s.no_solution} sans fichier solution.`;
}

async function loadAtelierStatus() {
  try {
    const s = await (await fetch("/atelier/status")).json();
    renderAtelierStatus(s);
    return s;
  } catch (e) { return null; }
}

async function runAtelierSync(btn) {
  const b = btn || $("#dashAtelierGo");
  const label = b ? b.textContent : "";
  if (b) { b.disabled = true; b.textContent = "File…"; }
  const out = $("#dashAtelierOut");
  let totFp = 0, totEcu = 0, totArch = 0, totErr = 0, loops = 0;
  try {
    while (loops < 400) {
      loops += 1;
      const d = await (await fetch("/atelier/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 12 }),
      })).json();
      totFp += d.fingerprints || 0;
      totEcu += d.ecu || 0;
      totArch += d.archived || 0;
      totErr += d.errors || 0;
      if (out) {
        out.textContent = `Lot ${loops} : ${d.processed} fiche(s) · `
          + `reste ${d.remaining} · empreintes +${totFp} · ECU +${totEcu} · archives +${totArch}`;
      }
      if (!d.remaining) break;
    }
    const s = await loadAtelierStatus();
    if (s && s.pending === 0 && s.missing_files === s.total) {
      alert("Aucun BIN sur cette machine. Pointe d'abord le dossier CARTOS, puis relance la file.");
    } else {
      alert(`File terminée.\nEmpreintes v2 : ${totFp}\nVersions ECU : ${totEcu}\nArchivées : ${totArch}\nErreurs : ${totErr}`);
    }
    vizSolLoaded = false; patchSolLoaded = false;
  } catch (e) {
    alert("Erreur file : " + e.message);
  }
  if (b) { b.disabled = false; b.textContent = label || "Lancer la file"; }
}

async function runCleanup(apply) {
  if (apply && !confirm("Supprimer les fiches suspectes (.cache…), fusionner les vrais doublons, rattacher les solutions orphelines ?\nLa base est d'abord sauvegardée.")) return;
  const d = await (await fetch("/atelier/cleanup", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apply }),
  })).json();
  const j = d.junk || {}, dup = d.duplicates || {}, ln = d.linked || {};
  const txt = (apply ? "Nettoyage appliqué" : "Aperçu — rien n'est écrit")
    + `\n${j.count} suspect(s)` + (apply ? ` (supprimé(s) : ${j.deleted})` : "")
    + `\n${dup.groups} groupe(s) doublon → ${dup.removed} fiche(s) en trop`
    + `\n${ln.linked} solution(s) rattachable(s) (${ln.empty} sans fichier solution)`;
  const el = $("#dashAtelierOut");
  if (el) el.textContent = txt.replace(/\n/g, " · ");
  alert(txt);
  if (apply) { vizSolLoaded = false; loadAtelierStatus(); initDash(); }
}

$("#dashAtelierGo") && ($("#dashAtelierGo").onclick = () => {
  if (!confirm("Lancer la file atelier ? On reprend tout seul si ça s'arrête.")) return;
  runAtelierSync($("#dashAtelierGo"));
});
$("#dashCleanupTest") && ($("#dashCleanupTest").onclick = () => runCleanup(false));
$("#dashCleanupApply") && ($("#dashCleanupApply").onclick = () => runCleanup(true));

async function loadSolutions() {
  $("#solList").innerHTML = `<div class="empty"><span class="spin"></span> Chargement…</div>`;
  const r = await fetch("/solutions");
  const d = await r.json();
  $("#dbSize").textContent = d.db_size;
  window.allSolutions = d.solutions;
  applyFilters();
}

/* catégories d'une fiche pour une dimension donnée (le type peut être multiple) */
function solCategories(s, dim) {
  if (dim === "type") {
    const t = (s.solution_type || "").trim();
    if (!t) return [SOL_DIM_EMPTY];
    return t.split(/\s*\+\s*/).map(x => x.trim()).filter(Boolean);
  }
  if (dim === "manufacturer") return [s.manufacturer || SOL_DIM_EMPTY];
  if (dim === "platform") return [s.ecu_platform || SOL_DIM_EMPTY];
  if (dim === "status") return [(STATUS[s.tested_status] || [s.tested_status])[0]];
  if (dim === "tags") {
    const t = (s.tags || "").split(",").map(x => x.trim()).filter(Boolean);
    return t.length ? t : ["(sans étiquette)"];
  }
  return [SOL_DIM_EMPTY];
}

function solText(s) {
  return [s.vehicle_label, s.solution_type, s.ecu_platform, s.ecu_version,
          s.manufacturer, s.notes, s.tags].filter(Boolean).join(" ").toLowerCase();
}

function applyFilters() {
  const all = window.allSolutions || [];
  const q = $("#solSearch").value.trim().toLowerCase();
  const dim = window.solDim;
  // 1) filtre texte
  const textList = q ? all.filter(s => solText(s).includes(q)) : all;
  // 2) facettes (comptage par catégorie) sur le résultat texte
  const counts = new Map();
  for (const s of textList)
    for (const c of solCategories(s, dim)) counts.set(c, (counts.get(c) || 0) + 1);
  renderFacets(counts, textList.length);
  // 3) filtre catégorie
  const list = window.solCat == null
    ? textList
    : textList.filter(s => solCategories(s, dim).includes(window.solCat));
  $("#solCount").textContent = `${list.length} / ${all.length} fiche(s)`;
  renderSolutions(list, $("#solDim2").value);
  updateBulkBar();
}

function renderFacets(counts, total) {
  const box = $("#solFacets");
  const entries = [...counts.entries()].sort((a, b) =>
    b[1] - a[1] || a[0].localeCompare(b[0], "fr"));
  window._facetEntries = entries;  // catégories brutes (non échappées)
  let html = `<button class="facet${window.solCat == null ? " on" : ""}" data-idx="-1">`
    + `Tous <span class="facet-n">${total}</span></button>`;
  html += entries.map(([cat, n], i) =>
    `<button class="facet${window.solCat === cat ? " on" : ""}" data-idx="${i}">`
    + `${esc(cat)} <span class="facet-n">${n}</span></button>`).join("");
  box.innerHTML = html;
  box.querySelectorAll(".facet").forEach(b => {
    b.onclick = () => {
      const i = parseInt(b.dataset.idx, 10);
      window.solCat = i < 0 ? null : window._facetEntries[i][0];
      applyFilters();
    };
  });
}

function rowHTML(s) {
  const [lbl, cls] = STATUS[s.tested_status] || [s.tested_status, ""];
  const sub = [s.solution_type, s.ecu_platform, s.ecu_version, s.manufacturer]
    .filter(Boolean).map(esc).join(" · ");
  const size = s.stock_size ? (s.stock_size / 1024).toFixed(0) + " Ko" : "";
  const tags = (s.tags || "").split(",").map(t => t.trim()).filter(Boolean);
  const tagsHTML = tags.length
    ? `<div class="sol-tags">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : "";
  return `<div class="sol-row" data-id="${s.id}">
    <input type="checkbox" class="solchk" data-id="${s.id}"${window.solSelected && window.solSelected.has(s.id) ? " checked" : ""}>
    <div class="sol-main">
      <div class="sol-title">${esc(s.vehicle_label) || "(sans libellé)"}
        <span class="badge ${cls}">${lbl}</span></div>
      <div class="sol-sub">${sub || '<span class="muted">—</span>'}</div>
      ${tagsHTML}
      ${s.notes ? `<div class="sol-notes">${esc(s.notes)}</div>` : ""}
    </div>
    <div class="sol-meta muted">${size}</div>
    <div class="sol-actions">
      ${s.solution_file ? `<a class="ghost sm" href="/solution/file?id=${s.id}">⬇ Solution</a>` : ""}
      <button class="ghost sm" data-act="edit">Modifier</button>
      <button class="ghost sm danger" data-act="del">Supprimer</button>
    </div>
  </div>`;
}

function wireRows(box) {
  const byId = new Map((window.allSolutions || []).map(s => [s.id, s]));
  box.querySelectorAll(".sol-row").forEach(row => {
    const sol = byId.get(+row.dataset.id);
    if (!sol) return;
    row.querySelector('[data-act="edit"]').onclick = () => openEdit(sol);
    row.querySelector('[data-act="del"]').onclick = () => delSolution(sol);
  });
  box.querySelectorAll(".solchk").forEach(c => {
    c.onchange = () => {
      const id = +c.dataset.id;
      if (c.checked) window.solSelected.add(id); else window.solSelected.delete(id);
      updateBulkBar();
    };
  });
}

window.solSelected = new Set();
function updateBulkBar() {
  const n = window.solSelected.size;
  $("#solBulk").classList.toggle("hidden", n === 0);
  $("#solBulkCount").textContent = `${n} fiche(s) sélectionnée(s)`;
}
$("#solBulkClear").onclick = () => { window.solSelected.clear(); updateBulkBar(); applyFilters(); };
$("#solBulkTagBtn").onclick = async () => {
  const tag = $("#solBulkTag").value.trim();
  if (!tag) return;
  await bulkApply({ add_tags: tag });
  $("#solBulkTag").value = "";
};
$("#solBulkStatusBtn").onclick = async () => {
  const st = $("#solBulkStatus").value;
  if (!st) return;
  await bulkApply({ set_status: st });
};
async function bulkApply(payload) {
  const ids = [...window.solSelected];
  if (!ids.length) return;
  await fetch("/solutions/bulk", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, ...payload }),
  });
  await loadSolutions();   // recharge la base à jour (garde la sélection)
}

function renderSolutions(list, dim2) {
  const box = $("#solList");
  if (!list.length) {
    box.innerHTML = `<div class="empty">Aucune solution${$("#solSearch").value || window.solCat ? " pour ce filtre" : " en base"}.</div>`;
    return;
  }
  if (!dim2) {
    box.innerHTML = list.map(rowHTML).join("");
    wireRows(box);
    return;
  }
  // sous-groupement : regroupe par catégorie de la dimension secondaire
  const groups = new Map();
  for (const s of list)
    for (const c of solCategories(s, dim2)) {
      if (!groups.has(c)) groups.set(c, []);
      groups.get(c).push(s);
    }
  const ordered = [...groups.entries()].sort((a, b) =>
    b[1].length - a[1].length || a[0].localeCompare(b[0], "fr"));
  box.innerHTML = ordered.map(([cat, rows]) =>
    `<div class="sol-group">
       <div class="sol-group-head">${esc(cat)} <span class="muted">· ${rows.length}</span></div>
       ${rows.map(rowHTML).join("")}
     </div>`).join("");
  wireRows(box);
}

/* ---- Édition ---- */
let editingId = null;
function openEdit(s) {
  editingId = s.id;
  $("#e_label").value = s.vehicle_label || "";
  $("#e_ecu").value = s.ecu_version || "";
  $("#e_platform").value = s.ecu_platform || "";
  $("#e_manufacturer").value = s.manufacturer || "";
  $("#e_type").value = s.solution_type || "";
  $("#e_status").value = s.tested_status || "a_confirmer";
  $("#e_tags").value = s.tags || "";
  $("#e_notes").value = s.notes || "";
  $("#editModal").classList.remove("hidden");
}
$("#e_cancel").onclick = () => $("#editModal").classList.add("hidden");
$("#e_save").onclick = async () => {
  await fetch("/solutions/update", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: editingId,
      vehicle_label: $("#e_label").value, ecu_version: $("#e_ecu").value,
      ecu_platform: $("#e_platform").value, manufacturer: $("#e_manufacturer").value,
      solution_type: $("#e_type").value, tested_status: $("#e_status").value,
      tags: $("#e_tags").value, notes: $("#e_notes").value,
    }),
  });
  $("#editModal").classList.add("hidden");
  loadSolutions();
};

/* ---- Suppression ---- */
async function delSolution(s) {
  if (!confirm(`Supprimer la solution « ${s.vehicle_label || s.ecu_version || s.id} » ?\n(Le fichier sur ton disque n'est pas touché.)`)) return;
  const r = await fetch("/solutions/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: s.id }),
  });
  const d = await r.json();
  $("#dbSize").textContent = d.db_size;
  loadSolutions();
}

/* ===================== Visualiseur (carte 2D + courbe) ===================== */
let vizSolLoaded = false;
window.vizSeries = [];
const VIZ_COLORS = ["#ff5a3c", "#39d98a", "#f5c451", "#5aa3ff"];

function vizInfo(html) { $("#viz_info").innerHTML = html; }

function vizUpdateControls() {
  const type = $("#viz_type").value;
  const src = $("#viz_source").value;
  $("#viz_disk_wrap").classList.toggle("hidden", src !== "disk");
  $("#viz_compare_wrap").classList.toggle("hidden", src !== "compare");
  $("#viz_diff_wrap").classList.toggle("hidden", src !== "compare");
  $("#viz_base_wrap").classList.toggle("hidden", src !== "base");
  $("#viz_which_wrap").classList.toggle("hidden", src !== "base");
  document.querySelectorAll(".viz-map-ctrl").forEach(e => e.classList.toggle("hidden", type !== "map"));
  document.querySelectorAll(".viz-curve-ctrl").forEach(e => e.classList.toggle("hidden", type !== "curve"));
  document.querySelectorAll(".viz-cartes-ctrl").forEach(e => e.classList.toggle("hidden", type !== "cartes"));
  $("#viz_render").textContent = type === "cartes" ? "Détecter" : "Afficher";
  const cartesWrap = $("#viz_cartes_wrap");
  if (cartesWrap) cartesWrap.classList.toggle("hidden", type !== "cartes");
  const panels = $("#viz_panels");
  if (panels) panels.classList.toggle("hidden", type !== "map");
  const curve = $("#viz_curve_wrap");
  if (curve) curve.classList.toggle("hidden", type !== "curve");
  const nav = $("#viz_nav");
  if (nav) nav.classList.toggle("hidden", type !== "curve");
}
$("#viz_type").addEventListener("change", vizUpdateControls);
$("#viz_source").addEventListener("change", vizUpdateControls);

async function initViz() {
  if (vizSolLoaded) return;
  const r = await fetch("/solutions");
  const d = await r.json();
  $("#viz_sol").innerHTML = d.solutions.map(s =>
    `<option value="${s.id}">${esc(s.vehicle_label || s.ecu_version || ("#" + s.id))}</option>`
  ).join("") || `<option value="">(base vide)</option>`;
  vizSolLoaded = true;
  vizUpdateControls();
}

function fileBytes(file) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(new Uint8Array(fr.result));
    fr.onerror = rej; fr.readAsArrayBuffer(file);
  });
}
async function fetchBytes(id, which) {
  const r = await fetch(`/viz/bytes?id=${id}&which=${which}`);
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || "lecture impossible"); }
  return new Uint8Array(await r.arrayBuffer());
}
function diffMaskOf(a, b) {
  const m = Math.min(a.length, b.length);
  const mask = new Uint8Array(b.length);
  let changed = 0;
  for (let i = 0; i < m; i++) if (a[i] !== b[i]) { mask[i] = 1; changed++; }
  for (let i = m; i < b.length; i++) { mask[i] = 1; changed++; }
  return { mask, changed };
}

/* charge les séries de données selon la source (réutilisé carte + courbe) */
async function vizLoadSeries(forCurve) {
  const src = $("#viz_source").value;
  if (src === "disk") {
    const f = $("#viz_file").files[0];
    if (!f) throw new Error("Choisis un fichier.");
    return [{ label: f.name, bytes: await fileBytes(f), color: VIZ_COLORS[0] }];
  }
  if (src === "compare") {
    const files = Array.from($("#viz_files").files);
    if (files.length < 2) throw new Error("Choisis au moins 2 fichiers.");
    if (files.length > 4) throw new Error("Maximum 4 fichiers.");
    const all = await Promise.all(files.map(fileBytes));
    const series = files.map((f, i) => ({ label: f.name, bytes: all[i], color: VIZ_COLORS[i % 4] }));
    if (!forCurve && $("#viz_diff_chk").checked && files.length === 2) {
      const { mask, changed } = diffMaskOf(all[0], all[1]);
      series[1].diffMask = mask; series.changed = changed;
    }
    return series;
  }
  // base
  const id = $("#viz_sol").value;
  if (!id) throw new Error("Aucune solution.");
  const which = $("#viz_which").value;
  if (which === "diff") {
    const s = await fetchBytes(id, "solution");
    let o = null;
    try { o = await fetchBytes(id, "original"); } catch (e) { o = null; }
    if (!o) {
      window.vizNote = "⚠ fichier original indisponible — solution seule affichée. "
        + "Onglet Solutions → « Réparer les liens » pour activer la comparaison.";
      return [{ label: "solution", bytes: s, color: VIZ_COLORS[0] }];
    }
    window.vizNote = "";
    if (forCurve) return [
      { label: "original", bytes: o, color: VIZ_COLORS[0] },
      { label: "solution", bytes: s, color: VIZ_COLORS[1] },
    ];
    const { mask, changed } = diffMaskOf(o, s);
    const r = [{ label: "différence", bytes: s, diffMask: mask, color: VIZ_COLORS[0] }];
    r.changed = changed; return r;
  }
  return [{ label: which, bytes: await fetchBytes(id, which), color: VIZ_COLORS[0] }];
}

/* ---------- Carte 2D (octet = pixel) ---------- */
function heat(v) {
  const t = v / 255;
  return [Math.round(255 * Math.min(1, Math.max(0, t * 2 - 0.5))),
          Math.round(255 * Math.min(1, Math.max(0, 1 - Math.abs(t - 0.5) * 2))),
          Math.round(255 * Math.min(1, Math.max(0, 1.5 - t * 2)))];
}
function drawByteMap(canvas, bytes, width, mode, diffMask) {
  const n = bytes.length, rows = Math.ceil(n / width) || 1;
  canvas.width = width; canvas.height = rows;
  const ctx = canvas.getContext("2d"), img = ctx.createImageData(width, rows), data = img.data;
  for (let i = 0; i < n; i++) {
    const p = i * 4;
    if (diffMask && diffMask[i]) { data[p] = 255; data[p + 1] = 90; data[p + 2] = 20; }
    else if (mode === "heat") { const c = heat(bytes[i]); data[p] = c[0]; data[p + 1] = c[1]; data[p + 2] = c[2]; }
    else { const v = bytes[i]; data[p] = v; data[p + 1] = v; data[p + 2] = v; }
    data[p + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
}
async function doMap() {
  const width = parseInt($("#viz_width").value, 10), mode = $("#viz_mode").value;
  vizInfo(`<span class="spin"></span> rendu…`);
  window.vizNote = "";
  const series = await vizLoadSeries(false);
  const wrap = $("#viz_panels"); wrap.innerHTML = "";
  const multi = series.length > 1;
  for (const s of series) {
    const col = document.createElement("div"); col.className = "viz-panel";
    const lab = document.createElement("div"); lab.className = "viz-panel-label"; lab.textContent = s.label;
    const cv = document.createElement("canvas"); if (multi) cv.style.maxWidth = "520px";
    col.appendChild(lab); col.appendChild(cv); wrap.appendChild(col);
    drawByteMap(cv, s.bytes, width, mode, s.diffMask);
  }
  const rows = Math.ceil(series[0].bytes.length / width);
  let extra = "";
  if (series.changed != null) extra = ` · ${series.changed.toLocaleString("fr")} octets diffèrent (orange)`;
  if (window.vizNote) extra += ` · ${window.vizNote}`;
  vizInfo(`${esc(series.map(s => s.label).join(" | "))} · ${width}×${rows} px${extra}`);
}

/* ---------- Cartes 2D (Kennfeld) ---------- */
window.vizMaps = [];
window.vizMapIdx = -1;
window.vizMapSpec = null;

function readU(bytes, off, bits, be) {
  const bps = bits / 8;
  if (off == null || off < 0 || off + bps > bytes.length) return null;
  if (bits === 8) return bytes[off];
  return be ? ((bytes[off] << 8) | bytes[off + 1]) : (bytes[off] | (bytes[off + 1] << 8));
}
function asSigned(v, bits) {
  if (v == null) return v;
  const half = 1 << (bits - 1);
  return v >= half ? v - (1 << bits) : v;
}
function decodeSpec(bytes, spec, signed) {
  const bits = +(spec.bits || 16), be = spec.endian === "be";
  const rows = +spec.rows, cols = +spec.cols;
  const x = spec.axis_x_off != null
    ? Array.from({ length: cols }, (_, i) => readU(bytes, spec.axis_x_off + i * (bits / 8), bits, be))
    : null;
  const y = spec.axis_y_off != null
    ? Array.from({ length: rows }, (_, i) => readU(bytes, spec.axis_y_off + i * (bits / 8), bits, be))
    : null;
  const z = [];
  let p = +spec.data_off;
  for (let r = 0; r < rows; r++) {
    const row = [];
    for (let c = 0; c < cols; c++) {
      let v = readU(bytes, p, bits, be);
      if (signed) v = asSigned(v, bits);
      row.push(v);
      p += bits / 8;
    }
    z.push(row);
  }
  let xOut = x;
  if (signed && xOut) xOut = xOut.map(v => asSigned(v, bits));
  let yOut = y;
  if (signed && yOut) yOut = yOut.map(v => asSigned(v, bits));
  return { x: xOut, y: yOut, z, bits, rows, cols };
}
function heatT(t) {
  t = Math.max(0, Math.min(1, t));
  const stops = [
    [15, 40, 90], [30, 120, 160], [40, 170, 90],
    [210, 190, 40], [230, 110, 20], [200, 40, 30],
  ];
  const x = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
  const a = stops[i], b = stops[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}
function luma(rgb) { return (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000; }

function renderMapList() {
  const box = $("#viz_maps_list");
  const maps = window.vizMaps || [];
  if (!maps.length) {
    box.innerHTML = `<div class="viz-grid-empty">Aucune carte 2D / 1D détectée.<br>
      Ajuste bits / endian puis redétecte, ou saisis offset + lignes × colonnes à droite.</div>`;
    return;
  }
  box.innerHTML = maps.map((m, i) => {
    const chg = m.changed ? `<span class="mi-chg">Δ ${m.changed_bytes} o</span>` : "";
    const kind = m.kind === "1d" ? "1D" : "2D";
    return `<button type="button" class="viz-map-item ${i === window.vizMapIdx ? "on" : ""}" data-i="${i}">
      <div class="mi-top"><span>${kind} ${m.rows}×${m.cols}</span>${chg}</div>
      <div class="mi-sub">0x${Number(m.offset).toString(16)} · ${m.bits}-bit ${m.endian.toUpperCase()}
        · ${m.zmin}–${m.zmax}</div>
    </button>`;
  }).join("");
  box.querySelectorAll(".viz-map-item").forEach(btn => {
    btn.onclick = () => selectMap(+btn.dataset.i);
  });
}

function specFromForm(base) {
  const t = $("#viz_map_off").value.trim();
  const off = t.toLowerCase().startsWith("0x") ? parseInt(t, 16) : parseInt(t, 10);
  const rows = parseInt($("#viz_map_rows").value, 10);
  const cols = parseInt($("#viz_map_cols").value, 10);
  const bits = parseInt($("#viz_map_bits").value, 10);
  const endian = $("#viz_map_endian").value;
  const bps = bits / 8;
  const spec = Object.assign({}, base || {}, { rows, cols, bits, endian });
  if (!isNaN(off)) {
    // si on a des axes, on les garde ; sinon offset = data
    if (spec.axis_x_off == null) spec.data_off = off;
    else {
      const shift = off - (spec.offset || spec.axis_x_off);
      if (spec.axis_x_off != null) spec.axis_x_off += shift;
      if (spec.axis_y_off != null) spec.axis_y_off += shift;
      spec.data_off = (spec.data_off || 0) + shift;
      spec.offset = off;
    }
  }
  if (spec.axis_x_off == null) {
    spec.data_off = isNaN(off) ? (spec.data_off || 0) : off;
    spec.offset = spec.data_off;
  }
  spec.end = (spec.data_off || 0) + rows * cols * bps;
  return spec;
}

function fillFormFromSpec(spec) {
  const off = spec.offset != null ? spec.offset : spec.data_off;
  $("#viz_map_off").value = "0x" + Number(off || 0).toString(16);
  $("#viz_map_rows").value = spec.rows;
  $("#viz_map_cols").value = spec.cols;
  if (spec.bits) $("#viz_map_bits").value = String(spec.bits);
  if (spec.endian) $("#viz_map_endian").value = spec.endian;
}

function selectMap(i, specOverride) {
  const maps = window.vizMaps || [];
  window.vizMapIdx = i;
  const spec = specOverride || maps[i];
  if (!spec) {
    renderMapGrid(null);
    return;
  }
  window.vizMapSpec = spec;
  fillFormFromSpec(spec);
  renderMapList();
  renderMapGrid(spec);
}

function mapFactor() {
  const sel = $("#viz_map_factor");
  if (!sel) return 1;
  if (sel.value === "custom") {
    const v = parseFloat($("#viz_map_factor_custom").value);
    return Number.isFinite(v) && v !== 0 ? v : 1;
  }
  const v = parseFloat(sel.value);
  return Number.isFinite(v) ? v : 1;
}
function mapZOffset() {
  const v = parseFloat(($("#viz_map_zoff") || {}).value);
  return Number.isFinite(v) ? v : 0;
}
function mapUnit() {
  return (($("#viz_map_unit") || {}).value || "").trim();
}
function physZ(v) {
  if (v == null) return v;
  return v * mapFactor() + mapZOffset();
}
function fmtPhys(v) {
  if (v == null || Number.isNaN(v)) return "";
  const p = physZ(v);
  return fmtNum(p);
}
function fmtNum(p) {
  if (p == null || Number.isNaN(p)) return "";
  if (Math.abs(p - Math.round(p)) < 1e-9) return String(Math.round(p));
  const a = Math.abs(p);
  const d = a >= 100 ? 1 : a >= 10 ? 2 : a >= 1 ? 3 : 4;
  return String(+p.toFixed(d));
}

function renderMapGrid(spec) {
  const host = $("#viz_map_grid");
  const meta = $("#viz_map_meta");
  const series = window.vizSeries || [];
  if (!spec || !series.length) {
    host.innerHTML = `<div class="viz-grid-empty">Rien à afficher.</div>`;
    meta.textContent = "";
    return;
  }
  const signed = $("#viz_map_signed").checked;
  const decoded = series.map(s => decodeSpec(s.bytes, spec, signed));
  const a = decoded[0];
  const b = decoded[1] || null;
  const extra = decoded.slice(2);
  const rows = a.rows, cols = a.cols;
  const factor = mapFactor(), zoff = mapZOffset(), unit = mapUnit();
  const physOn = factor !== 1 || zoff !== 0;
  let mn = Infinity, mx = -Infinity, deltas = 0;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const v = physZ(a.z[r][c]);
    if (v != null) { if (v < mn) mn = v; if (v > mx) mx = v; }
    if (b && b.z[r][c] != null) {
      const vb = physZ(b.z[r][c]);
      if (vb < mn) mn = vb;
      if (vb > mx) mx = vb;
      if (b.z[r][c] !== a.z[r][c]) deltas++;
    }
    extra.forEach(ex => {
      if (ex.z[r][c] != null && ex.z[r][c] !== a.z[r][c]) {
        const ve = physZ(ex.z[r][c]);
        if (ve < mn) mn = ve;
        if (ve > mx) mx = ve;
        deltas++;
      }
    });
  }
  if (!isFinite(mn)) { mn = 0; mx = 1; }
  const rng = mx - mn || 1;
  const x = a.x, y = a.y;
  let html = `<table class="viz-grid"><thead><tr><th></th>`;
  for (let c = 0; c < cols; c++) html += `<th>${x && x[c] != null ? x[c] : c}</th>`;
  html += `</tr></thead><tbody>`;
  for (let r = 0; r < rows; r++) {
    html += `<tr><th>${y && y[r] != null ? y[r] : r}</th>`;
    for (let c = 0; c < cols; c++) {
      const v0 = a.z[r][c], v1 = b ? b.z[r][c] : null;
      const others = extra.map((ex, i) => ({ v: ex.z[r][c], label: series[i + 2].label })).filter(o => o.v != null && o.v !== v0);
      const show = v1 != null ? v1 : v0;
      const t = (physZ(show) - mn) / rng;
      const rgb = heatT(t);
      const fg = luma(rgb) > 150 ? "#0e1116" : "#f4f7fb";
      const chg = (b && v0 !== v1) || others.length;
      const extraTip = others.map(o => `${o.label}:${fmtPhys(o.v)}`).join(" · ");
      const inner = chg
        ? `<span class="old">${fmtPhys(v0)}</span>${fmtPhys(show)}${others.length ? "<small>+</small>" : ""}`
        : `${fmtPhys(show)}`;
      html += `<td class="${chg ? "delta" : ""}" style="background:rgb(${rgb.join(",")});color:${fg}"
        title="${chg ? fmtPhys(v0) + " → " + fmtPhys(show) + (extraTip ? " · " + extraTip : "") : fmtPhys(show)}">${inner}</td>`;
    }
    html += `</tr>`;
  }
  html += `</tbody></table>`;
  host.innerHTML = html;
  const kind = spec.kind === "1d" ? "courbe 1D" : "Kennfeld 2D";
  const cmp = series.length > 1
    ? ` · ${series.map(s => esc(s.label)).join(" / ")} · ${deltas} cellule(s) touchée(s)`
    : "";
  const phys = physOn
    ? ` · facteur ${factor}${zoff ? " · offset " + zoff : ""}${unit ? " · " + esc(unit) : ""}`
    : " · valeurs brutes (pas de Damos)";
  meta.innerHTML = `${kind} · ${rows}×${cols} · data 0x${Number(spec.data_off).toString(16)}
    · ${spec.bits}-bit ${spec.endian.toUpperCase()}${signed ? " signé" : ""}
    · Z ${fmtNum(mn)}–${fmtNum(mx)}${cmp}${phys}`;
}

function csvEscape(s) {
  s = String(s ?? "");
  if (/[;"\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}
function csvOfGrid(a, b) {
  const factor = mapFactor(), zoff = mapZOffset(), unit = mapUnit();
  const spec = window.vizMapSpec || {};
  const rows = a.rows, cols = a.cols;
  const x = a.x, y = a.y;
  const lines = [];
  const title = [
    "Carto Matcher 2D",
    spec.data_off != null ? "data 0x" + Number(spec.data_off).toString(16) : "",
    rows + "x" + cols,
    "facteur=" + factor,
    "offset=" + zoff,
    unit ? "unite=" + unit : "",
  ].filter(Boolean).join(" · ");
  lines.push("# " + title);
  const header = [""].concat(Array.from({ length: cols }, (_, c) => x && x[c] != null ? x[c] : c));
  const emit = (grid, withOffset) => {
    const off = withOffset ? zoff : 0;
    const out = [header.map(csvEscape).join(";")];
    for (let r = 0; r < rows; r++) {
      const yv = y && y[r] != null ? y[r] : r;
      const cells = [csvEscape(yv)];
      for (let c = 0; c < cols; c++) cells.push(csvEscape(fmtNum((grid[r][c] * factor) + off)));
      out.push(cells.join(";"));
    }
    return out;
  };
  if (!b) {
    lines.push.apply(lines, emit(a.z, true));
  } else {
    lines.push("# original");
    lines.push.apply(lines, emit(a.z, true));
    lines.push("");
    lines.push("# solution");
    lines.push.apply(lines, emit(b.z, true));
    lines.push("");
    lines.push("# delta (solution - original)");
    const delta = a.z.map((row, r) => row.map((v, c) => (b.z[r][c] - v)));
    lines.push.apply(lines, emit(delta, false));
  }

  return lines.join("\n") + "\n";
}
function downloadText(name, text, mime) {
  const blob = new Blob([text], { type: mime || "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function exportCurrentCsv() {
  const spec = window.vizMapSpec;
  const series = window.vizSeries || [];
  if (!spec || !series.length) { alert("Aucune carte à exporter."); return; }
  const signed = $("#viz_map_signed").checked;
  const a = decodeSpec(series[0].bytes, spec, signed);
  const b = series[1] ? decodeSpec(series[1].bytes, spec, signed) : null;
  downloadText("carte.csv", csvOfGrid(a, b));
}
function exportAllCsv() {
  const maps = window.vizMaps || [];
  const series = window.vizSeries || [];
  if (!maps.length || !series.length) { alert("Aucune carte à exporter."); return; }
  const signed = $("#viz_map_signed").checked;
  const changed = maps.filter(m => m.changed);
  const pool = changed.length ? changed : maps;
  const chunks = [];
  pool.forEach((spec, i) => {
    const a = decodeSpec(series[0].bytes, spec, signed);
    const b = series[1] ? decodeSpec(series[1].bytes, spec, signed) : null;
    chunks.push(csvOfGrid(a, b).replace(
      /^# Carto Matcher 2D/,
      `# carte ${i + 1} (${spec.kind || "2d"} ${spec.rows}x${spec.cols})`));
  });
  downloadText(changed.length ? "cartes_delta.csv" : "cartes.csv", chunks.join("\n"));
}



async function doCartes(preset) {
  vizInfo(`<span class="spin"></span> détection des cartes…`);
  window.vizNote = "";
  const src = $("#viz_source").value;
  const fd = new FormData();
  fd.append("bits", $("#viz_map_bits").value);
  fd.append("endian", $("#viz_map_endian").value);
  if (src === "disk") {
    const f = $("#viz_file").files[0];
    if (!f) throw new Error("Choisis un fichier.");
    fd.append("file", f);
    window.vizSeries = [{ label: f.name, bytes: await fileBytes(f), color: VIZ_COLORS[0] }];
  } else if (src === "compare") {
    const files = Array.from($("#viz_files").files);
    if (files.length < 2) throw new Error("Choisis au moins 2 fichiers.");
    fd.append("file", files[0]);
    fd.append("file2", files[1]);
    const all = await Promise.all(files.slice(0, 2).map(fileBytes));
    window.vizSeries = [
      { label: files[0].name, bytes: all[0], color: VIZ_COLORS[0] },
      { label: files[1].name, bytes: all[1], color: VIZ_COLORS[1] },
    ];
  } else {
    const id = $("#viz_sol").value;
    if (!id) throw new Error("Aucune solution.");
    fd.append("id", id);
    fd.append("which", $("#viz_which").value);
    const series = [];
    try { series.push({ label: "original", bytes: await fetchBytes(id, "original"), color: VIZ_COLORS[0] }); }
    catch (e) { /* original optionnel */ }
    try { series.push({ label: "solution", bytes: await fetchBytes(id, "solution"), color: VIZ_COLORS[1] }); }
    catch (e) { /* */ }
    if (!series.length) throw new Error("Fichiers de la fiche introuvables. Réparer les liens ?");
    window.vizSeries = series;
  }
  const r = await fetch("/maps/scan", { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "scan impossible");
  window.vizMaps = d.maps || [];
  const nChg = d.changed_maps || window.vizMaps.filter(m => m.changed).length;
  const note = d.header_len
    ? ` · header ${d.header_tool || ""} +0x${Number(d.header_len).toString(16)} retiré`
    : "";
  vizInfo(`${window.vizMaps.length} carte(s) · ${d.bits}-bit ${d.endian.toUpperCase()}
    · corps ${Number(d.body_size).toLocaleString("fr")} o${nChg ? " · " + nChg + " modifiée(s)" : ""}${note}`);
  const idx = preset
    ? 0
    : Math.max(0, window.vizMaps.findIndex(m => m.changed));
  if (preset) {
    window.vizMaps.unshift(preset);
    selectMap(0, preset);
  } else if (window.vizMaps.length) {
    selectMap(idx);
  } else {
    window.vizMapIdx = -1;
    renderMapList();
    renderMapGrid(null);
  }
}

$("#viz_map_apply").onclick = () => {
  if (!window.vizSeries.length) return;
  const spec = specFromForm(window.vizMapSpec || { kind: "2d" });
  window.vizMapSpec = spec;
  renderMapGrid(spec);
};
$("#viz_map_swap").onclick = () => {
  const spec = specFromForm(window.vizMapSpec || {});
  const rows = spec.rows, cols = spec.cols;
  spec.rows = cols; spec.cols = rows;
  const xo = spec.axis_x_off, yo = spec.axis_y_off;
  spec.axis_x_off = yo; spec.axis_y_off = xo;
  $("#viz_map_rows").value = spec.rows;
  $("#viz_map_cols").value = spec.cols;
  window.vizMapSpec = spec;
  renderMapGrid(spec);
};
$("#viz_map_signed").onchange = () => { if (window.vizMapSpec) renderMapGrid(window.vizMapSpec); };
$("#viz_map_factor").onchange = () => {
  const custom = $("#viz_map_factor").value === "custom";
  $("#viz_map_factor_custom_wrap").classList.toggle("hidden", !custom);
  if (window.vizMapSpec) renderMapGrid(window.vizMapSpec);
};
$("#viz_map_factor_custom").oninput = () => { if (window.vizMapSpec) renderMapGrid(window.vizMapSpec); };
$("#viz_map_zoff").oninput = () => { if (window.vizMapSpec) renderMapGrid(window.vizMapSpec); };
$("#viz_map_unit").oninput = () => { if (window.vizMapSpec) renderMapGrid(window.vizMapSpec); };
$("#viz_map_csv").onclick = exportCurrentCsv;
$("#viz_map_csv_all").onclick = exportAllCsv;


async function openZoneMap(i) {
  const pd = window.patchData;
  if (!pd || !pd.zones || !pd.zones[i]) return;
  const z = pd.zones[i];
  let spec = z.map;
  if (!spec) {
    const fd = new FormData();
    fd.append("file", pd.clientFile);
    fd.append("off", z.off);
    fd.append("len", z.len);
    const r = await fetch("/maps/guess", { method: "POST", body: fd });
    if (r.ok) spec = await r.json();
  }
  if (!spec) {
    alert("Cette zone n'a pas la forme d'une table 2D (axes introuvables).");
    return;
  }
  await initViz();
  $("#viz_type").value = "cartes";
  $("#viz_source").value = "disk";
  vizUpdateControls();
  showView("viz");
  $("#viz_cartes_wrap").classList.remove("hidden");
  $("#viz_panels").classList.add("hidden");
  $("#viz_curve_wrap").classList.add("hidden");
  $("#viz_nav").classList.add("hidden");
  const before = await fileBytes(pd.clientFile);
  let after = null;
  try {
    const solId = pd.solutionId;
    after = await fetchBytes(solId, "solution");
  } catch (e) { after = null; }
  window.vizSeries = after
    ? [{ label: "client", bytes: before, color: VIZ_COLORS[0] },
       { label: "solution", bytes: after, color: VIZ_COLORS[1] }]
    : [{ label: "client", bytes: before, color: VIZ_COLORS[0] }];
  spec.changed = true;
  window.vizMaps = [spec];
  fillFormFromSpec(spec);
  vizInfo(`Zone #${z.i + 1} · 0x${z.off.toString(16)} · ${spec.rows}×${spec.cols}`);
  selectMap(0, spec);
}

/* ---------- Courbe (valeur en fonction de l'adresse) ---------- */
function sampleAt(bytes, idx, bits, be) {
  const o = idx * (bits / 8);
  if (bits === 8) return bytes[o];
  if (bits === 16) return be ? ((bytes[o] << 8) | bytes[o + 1]) : ((bytes[o + 1] << 8) | bytes[o]);
  const b0 = bytes[o], b1 = bytes[o + 1], b2 = bytes[o + 2], b3 = bytes[o + 3];
  return be ? (b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
            : (b3 * 16777216 + b2 * 65536 + b1 * 256 + b0);
}
let curveStart = 0, curveCount = 512;
function curveBps() { return parseInt($("#viz_bits").value, 10) / 8; }
function curveTotal() {
  const bps = curveBps();
  return Math.min(...window.vizSeries.map(s => Math.floor(s.bytes.length / bps)));
}

function renderCurve() {
  const series = window.vizSeries;
  if (!series || !series.length) return;
  const bits = parseInt($("#viz_bits").value, 10), be = $("#viz_endian").value === "be";
  const bps = bits / 8, maxVal = Math.pow(2, bits) - 1;
  const total = curveTotal();
  curveCount = Math.max(16, Math.min(curveCount, total));
  const maxStart = Math.max(0, total - curveCount);
  if (curveStart > maxStart) curveStart = maxStart;
  if (curveStart < 0) curveStart = 0;
  const slider = $("#viz_pos"); slider.max = maxStart; slider.value = curveStart;

  const canvas = $("#viz_curve_canvas"), W = 1100, H = 440, PADL = 50, PADB = 18;
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#0e1116"; ctx.fillRect(0, 0, W, H);
  const plotW = W - PADL, plotH = H - PADB;
  // bandes des zones modifiées (diff), sous la grille et les courbes
  const regs = window.vizDiffRegions || [];
  for (let k = 0; k < regs.length; k++) {
    const s0 = regs[k][0] / bps, s1 = (regs[k][1] + 1) / bps;   // bornes en échantillons
    const a = Math.max(s0, curveStart), b = Math.min(s1, curveStart + curveCount);
    if (b <= a) continue;
    const x0 = PADL + (a - curveStart) / curveCount * plotW;
    const w = Math.max(1.5, (b - a) / curveCount * plotW);
    const cur = k === window.vizDiffIdx;
    ctx.fillStyle = cur ? "rgba(255,90,30,0.30)" : "rgba(255,150,60,0.13)";
    ctx.fillRect(x0, 0, w, plotH);
    if (cur) { ctx.strokeStyle = "rgba(255,120,40,0.85)"; ctx.lineWidth = 1; ctx.strokeRect(x0, 0, w, plotH); }
  }
  // grille + axe Y
  ctx.font = "10px monospace"; ctx.textBaseline = "middle";
  for (let g = 0; g <= 4; g++) {
    const y = plotH - g / 4 * plotH;
    ctx.strokeStyle = "#2a3242"; ctx.beginPath(); ctx.moveTo(PADL, y); ctx.lineTo(W, y); ctx.stroke();
    ctx.fillStyle = "#8b97a7"; ctx.textAlign = "right"; ctx.fillText(Math.round(maxVal * g / 4), PADL - 6, y);
  }
  // axe X (offsets)
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let g = 0; g <= 8; g++) {
    const x = PADL + g / 8 * plotW, off = (curveStart + Math.round(g / 8 * curveCount)) * bps;
    ctx.strokeStyle = "#1c2230"; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke();
    ctx.fillStyle = "#8b97a7"; ctx.fillText("0x" + off.toString(16), x, plotH + 3);
  }
  const start = curveStart, count = curveCount;
  const envelope = count > plotW;   // plus de valeurs que de pixels -> min/max
  for (const s of series) {
    ctx.strokeStyle = s.color; ctx.lineWidth = 1;
    if (!envelope) {
      ctx.beginPath();
      for (let i = 0; i < count && start + i < total; i++) {
        const v = sampleAt(s.bytes, start + i, bits, be);
        const x = PADL + (count > 1 ? i / (count - 1) : 0) * plotW;
        const y = plotH - v / maxVal * plotH;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.stroke();
    } else {
      const spp = count / plotW;
      const colMin = new Float64Array(plotW).fill(Infinity);
      const colMax = new Float64Array(plotW).fill(-Infinity);
      for (let i = 0; i < count && start + i < total; i++) {
        const v = sampleAt(s.bytes, start + i, bits, be);
        let c = (i / spp) | 0; if (c >= plotW) c = plotW - 1;
        if (v < colMin[c]) colMin[c] = v;
        if (v > colMax[c]) colMax[c] = v;
      }
      ctx.beginPath();
      for (let c = 0; c < plotW; c++) {
        if (colMax[c] === -Infinity) continue;
        const x = PADL + c + 0.5;
        ctx.moveTo(x, plotH - colMin[c] / maxVal * plotH);
        ctx.lineTo(x, plotH - colMax[c] / maxVal * plotH);
      }
      ctx.stroke();
    }
  }
  const leg = series.map(s => `<span style="color:${s.color}">■</span> ${esc(s.label)}`).join("&nbsp;&nbsp;");
  $("#viz_pos_label").innerHTML = `${leg} &nbsp;·&nbsp; offset 0x${(start * bps).toString(16)} `
    + `· ${count.toLocaleString("fr")} valeurs ${bits}-bit ${be ? "BE" : "LE"}`
    + `${envelope ? " (enveloppe min/max)" : ""} · total ${total.toLocaleString("fr")}`;
}

function zoomCurve(factor, centerSample) {
  const total = curveTotal();
  const newCount = Math.max(16, Math.min(Math.round(curveCount * factor), total));
  if (centerSample == null) centerSample = curveStart + curveCount / 2;
  curveStart = Math.round(centerSample - (centerSample - curveStart) * (newCount / curveCount));
  curveCount = newCount;
  renderCurve();
}

async function doCurve() {
  vizInfo(`<span class="spin"></span> chargement…`);
  window.vizNote = "";
  window.vizSeries = await vizLoadSeries(true);
  setupCurveView();
}

function setupCurveView() {
  const w = $("#viz_window").value;
  curveCount = (w === "all") ? curveTotal() : parseInt(w, 10);
  curveStart = 0;
  // zones de différence si 2 séries (avant/après, original/solution, 2 fichiers)
  if (window.vizSeries.length === 2) {
    window.vizDiffRegions = diffRegions(window.vizSeries[0].bytes, window.vizSeries[1].bytes, 64);
  } else {
    window.vizDiffRegions = [];
  }
  window.vizDiffIdx = -1;
  const hasDiff = window.vizDiffRegions.length > 0;
  $("#viz_difffirst").classList.toggle("hidden", !hasDiff);
  $("#viz_diffprev").classList.toggle("hidden", !hasDiff);
  $("#viz_diffnext").classList.toggle("hidden", !hasDiff);
  $("#viz_difflast").classList.toggle("hidden", !hasDiff);
  $("#viz_nav").classList.remove("hidden");
  const note = window.vizNote ? ` · ${window.vizNote}` : "";
  const dn = hasDiff ? ` · ${window.vizDiffRegions.length} zone(s) de différence surlignées (◀ Δ / Δ ▶)` : "";
  vizInfo(`Courbe — molette pour zoomer, barre pour défiler.${dn}${note}`);
  renderCurve();
}

/* Visualiser le résultat de l'auto-patch : client (avant) vs patché (après) */
async function visualizeFromPatch() {
  const pd = window.patchData;
  if (!pd || !pd.clientFile) return;
  const before = await fileBytes(pd.clientFile);
  const fd = new FormData();
  fd.append("file", pd.clientFile);
  fd.append("id", pd.solutionId);
  fd.append("partial", $("#patch_partial").checked ? "1" : "0");
  fd.append("preview", "1");
  const r = await fetch("/patch/apply", { method: "POST", body: fd });
  if (!r.ok) { alert("Impossible de prévisualiser le patch."); return; }
  const after = new Uint8Array(await r.arrayBuffer());
  window.vizNote = "aperçu auto-patch — non enregistré";
  window.vizSeries = [
    { label: "client (avant)", bytes: before, color: VIZ_COLORS[0] },
    { label: "patché (après)", bytes: after, color: VIZ_COLORS[1] },
  ];
  $("#viz_type").value = "curve";
  showView("viz");
  vizUpdateControls();
  $("#viz_panels").classList.add("hidden");
  $("#viz_cartes_wrap").classList.add("hidden");
  $("#viz_curve_wrap").classList.remove("hidden");
  setupCurveView();
}

function diffRegions(a, b, gap) {
  const n = Math.max(a.length, b.length), regs = [];
  let start = -1, last = -1;
  for (let i = 0; i < n; i++) {
    const av = i < a.length ? a[i] : -1, bv = i < b.length ? b[i] : -2;
    if (av !== bv) { if (start < 0) start = i; last = i; }
    else if (start >= 0 && i - last > gap) { regs.push([start, last]); start = -1; }
  }
  if (start >= 0) regs.push([start, last]);
  return regs;
}

function gotoDiff(dir, absIdx) {
  const regs = window.vizDiffRegions || [];
  if (!regs.length) return;
  let idx;
  if (absIdx != null) {
    idx = absIdx < 0 ? regs.length + absIdx : absIdx;   // -1 = dernière
  } else {
    idx = (window.vizDiffIdx == null ? -1 : window.vizDiffIdx) + dir;
  }
  idx = Math.max(0, Math.min(regs.length - 1, idx));
  window.vizDiffIdx = idx;
  const bps = curveBps();
  const regStartSample = Math.floor(regs[idx][0] / bps);
  curveStart = Math.max(0, regStartSample - Math.floor(curveCount / 4));
  renderCurve();
  $("#viz_pos_label").innerHTML =
    `Δ ${idx + 1}/${regs.length} · offset 0x${regs[idx][0].toString(16)} `
    + `(longueur ${(regs[idx][1] - regs[idx][0] + 1)} octets) — ` + $("#viz_pos_label").innerHTML;
}

/* navigation / zoom courbe */
$("#viz_render").onclick = async () => {
  const type = $("#viz_type").value;
  $("#viz_panels").classList.toggle("hidden", type !== "map");
  $("#viz_curve_wrap").classList.toggle("hidden", type !== "curve");
  $("#viz_nav").classList.toggle("hidden", type !== "curve");
  $("#viz_cartes_wrap").classList.toggle("hidden", type !== "cartes");
  try {
    if (type === "map") await doMap();
    else if (type === "cartes") await doCartes();
    else await doCurve();
  }
  catch (e) { vizInfo("Erreur : " + e.message); }
};
$("#viz_pos").addEventListener("input", () => { curveStart = parseInt($("#viz_pos").value, 10); renderCurve(); });
$("#viz_prev").onclick = () => { curveStart -= curveCount; renderCurve(); };
$("#viz_next").onclick = () => { curveStart += curveCount; renderCurve(); };
$("#viz_difffirst").onclick = () => gotoDiff(0, 0);
$("#viz_diffprev").onclick = () => gotoDiff(-1);
$("#viz_diffnext").onclick = () => gotoDiff(1);
$("#viz_difflast").onclick = () => gotoDiff(0, -1);
$("#viz_zoomin").onclick = () => zoomCurve(0.5, null);
$("#viz_zoomout").onclick = () => zoomCurve(2, null);
$("#viz_window").addEventListener("change", () => {
  if (!window.vizSeries.length) return;
  const w = $("#viz_window").value;
  curveCount = (w === "all") ? curveTotal() : parseInt(w, 10);
  renderCurve();
});
["viz_bits", "viz_endian"].forEach(id =>
  $("#" + id).addEventListener("change", () => { if (window.vizSeries.length) renderCurve(); }));
$("#viz_go").onclick = () => {
  const t = $("#viz_addr").value.trim(); if (!t) return;
  const off = t.toLowerCase().startsWith("0x") ? parseInt(t, 16) : parseInt(t, 10);
  if (isNaN(off)) return;
  curveStart = Math.floor(off / curveBps()); renderCurve();
};
$("#viz_curve_canvas").addEventListener("wheel", (e) => {
  if (!window.vizSeries.length) return;
  e.preventDefault();
  const rect = e.currentTarget.getBoundingClientRect();
  const PADL = 50, W = 1100, plotW = W - PADL;
  const cx = (e.clientX - rect.left) / rect.width * W;       // coord canvas
  const frac = Math.max(0, Math.min(1, (cx - PADL) / plotW));
  const centerSample = curveStart + frac * curveCount;
  zoomCurve(e.deltaY > 0 ? 1.5 : 1 / 1.5, centerSample);
}, { passive: false });

/* ===================== Auto-patch ===================== */
let patchSolLoaded = false;
window.patchData = null;
window.patchSolutions = [];

async function initPatch() {
  if (!patchSolLoaded) {
    const r = await fetch("/solutions");
    const d = await r.json();
    window.patchSolutions = d.solutions;
    $("#patch_sol").innerHTML = d.solutions.map(s =>
      `<option value="${s.id}">${esc(s.vehicle_label || s.ecu_version || ("#" + s.id))}</option>`
    ).join("") || `<option value="">(base vide)</option>`;
    patchSolLoaded = true;
  }
  await fillPatchDossiers();
}

function bytesEqualRange(a, b, off, len) {
  for (let i = 0; i < len; i++) if (a[off + i] !== b[off + i]) return false;
  return true;
}

/* caractérisation locale d'une zone (sur l'original) + motif de changement */
function zoneFeatures(orig, sol, off, len) {
  const bits = (len >= 8 && len % 2 === 0) ? 16 : 8;
  const vals = [];
  if (bits === 16) for (let i = 0; i < len; i += 2) vals.push(orig[off + i] | (orig[off + i + 1] << 8));
  else for (let i = 0; i < len; i++) vals.push(orig[off + i]);
  let min = Infinity, max = -Infinity, sumAbs = 0, signChanges = 0, incr = 0, prevSign = 0;
  for (let i = 0; i < vals.length; i++) {
    if (vals[i] < min) min = vals[i];
    if (vals[i] > max) max = vals[i];
    if (i > 0) {
      const dlt = vals[i] - vals[i - 1];
      sumAbs += Math.abs(dlt);
      if (dlt >= 0) incr++;
      const sg = Math.sign(dlt);
      if (sg !== 0 && prevSign !== 0 && sg !== prevSign) signChanges++;
      if (sg !== 0) prevSign = sg;
    }
  }
  const range = max - min;
  const avgDelta = vals.length > 1 ? sumAbs / (vals.length - 1) : 0;
  const smooth = range > 0 ? avgDelta / range : 0;
  const monotonic = vals.length > 3 && incr >= (vals.length - 1) * 0.92 && range > 0;
  let shape;
  if (range === 0) shape = "constant";
  else if (monotonic) shape = "monotonic";
  else if (smooth < 0.18 && len >= 16) shape = "smooth";
  else shape = "noisy";

  // motif de changement (sur la solution dans la zone)
  let allZero = true, allFF = true, allSame = true;
  const first = sol[off];
  for (let i = 0; i < len; i++) {
    const b = sol[off + i];
    if (b !== 0) allZero = false;
    if (b !== 0xFF) allFF = false;
    if (b !== first) allSame = false;
  }
  let change = "mixed";
  if (allZero) change = "zeroed";
  else if (allFF) change = "ff";
  else if (allSame) change = "constant";

  // plage de la solution dans la zone
  let smin = Infinity, smax = -Infinity;
  for (let i = 0; i < len; i++) { const b = sol[off + i]; if (b < smin) smin = b; if (b > smax) smax = b; }

  return { bits, shape, change, origRange: [min, max], solRange: [smin, smax],
           signChanges, smooth: +smooth.toFixed(3) };
}

function ficheContext(ficheType) {
  const t = (ficheType || "").toLowerCase();
  if (/fap|dpf/.test(t)) return " · contexte FAP/DPF";
  if (/scr|adblue/.test(t)) return " · contexte AdBlue/SCR";
  if (/egr/.test(t)) return " · contexte EGR";
  if (/stage|stg/.test(t)) return " · contexte Stage";
  if (/dtc|mil/.test(t)) return " · contexte DTC/voyant";
  return "";
}

function localZoneLabel(f, ficheType, len) {
  let shapeWord;
  if (f.shape === "monotonic") shapeWord = "axe (croissant)";
  else if (f.shape === "constant") shapeWord = "scalaire / drapeau";
  else if (f.shape === "smooth") shapeWord = "table / cartographie";
  else if (len <= 4) shapeWord = "scalaire / limiteur";
  else shapeWord = "données";
  const ch = { zeroed: "mise à 0", ff: "0xFF", constant: "valeur forcée", mixed: "" }[f.change];
  return shapeWord + (ch ? " — " + ch : "") + ficheContext(ficheType);
}

function selectedFiche() {
  const id = +$("#patch_sol").value;
  return (window.patchSolutions || []).find(s => s.id === id) || {};
}

function renderPatchReport() {
  const pd = window.patchData;
  const rep = $("#patch_report");
  if (!pd) { rep.innerHTML = ""; return; }
  if (pd.multi && (!pd.zones || !pd.zones.length)) {
    const fl = (pd.fiches || []).map(f =>
      `<li>#${f.id} · ${esc(f.solution_type || "")} · ${esc(f.verdict || f.error || "")}
        · ${f.matched || 0}/${(f.zones || []).length} zones</li>`).join("");
    const cl = (pd.conflicts || []).slice(0, 20).map(c =>
      `<li>0x${Number(c.a_off).toString(16)} ${esc(c.a_type)} ↔ ${esc(c.b_type)} (${c.overlap} o)</li>`).join("");
    rep.innerHTML = `${pd.autoNote ? `<div class="patch-auto">${esc(pd.autoNote)}</div>` : ""}
      <div class="patch-client">Fichier client : <b>${esc(pd.clientName)}</b>
        ${pd.header_note ? `<div class="muted small">${esc(pd.header_note)}</div>` : ""}</div>
      ${pd.rsa_note ? `<div class="patch-rsa">${esc(pd.rsa_note)}</div>` : ""}
      <div class="${pd.vcls}">${esc(pd.verdict_text || pd.verdict || "")}</div>
      <div class="patch-stats">${esc(pd.stats || "")} · ${esc(pd.combined_type || "")}</div>
      <ul class="combo-list">${fl}</ul>
      ${cl ? `<div class="patch-warn">Conflits</div><ul class="combo-list">${cl}</ul>` : ""}
      <div class="patch-note">${esc((pd.checksum && pd.checksum.note) || "")}</div>
      <p><button type="button" class="ghost" id="combo2d">Cartes 2D multi-SOL</button></p>`;
    const c2 = $("#combo2d");
    if (c2) c2.onclick = openComboMaps;
    return;
  }
  const aiOn = pd.aiLabels && pd.aiLabels.length;
  const aiMap = {};
  if (aiOn) for (const l of pd.aiLabels) aiMap[l.i] = l;

  const rows = pd.zones.slice(0, 60).map(z => {
    const ai = aiMap[z.i];
    const aiCell = aiOn
      ? `<td>${ai ? esc(ai.label) + (ai.confidence ? ` <span class="muted">(${esc(ai.confidence)})</span>` : "") : "—"}</td>`
      : "";
    return `<tr class="${z.ok && !z.rsa ? "" : "zbad"}">
      <td>${z.i + 1}</td><td>0x${z.off.toString(16)}</td><td>${z.len} o</td>
      <td>${z.rsa ? "RSA — ignorée" : (z.ok ? "compatible" : "⚠ diffère")}</td>
      <td><span class="zchip zchip-${esc((z.zone && z.zone.code) || "data")}">${esc(z.localLabel)}</span></td>${aiCell}
      <td>${z.map
        ? `<button type="button" class="ghost sm" data-zi="${z.i}">2D ${z.map.rows}×${z.map.cols}</button>`
        : "—"}</td></tr>`;

  }).join("");

  rep.innerHTML = `${pd.autoNote ? `<div class="patch-auto">${esc(pd.autoNote)}</div>` : ""}
    <div class="patch-client">Fichier client : <b>${esc(pd.clientName)}</b>
      ${pd.header_note ? `<div class="muted small">${esc(pd.header_note)}</div>` : ""}</div>
    ${pd.rsa_note ? `<div class="patch-rsa">${esc(pd.rsa_note)}</div>` : ""}
    <div class="${pd.vcls}">${esc((pd.verdict_text || pd.verdict || "").replace(/[✅🟡🔴]/g, "").trim())}</div>
    <div class="patch-stats">${esc(pd.stats || "")}</div>
    <table class="patch-table"><thead><tr><th>#</th><th>Offset</th><th>Taille</th><th>État</th>
      <th>Type (local)</th>${aiOn ? "<th>Type (IA)</th>" : ""}<th>Carte</th></tr></thead>
    <tbody>${rows}</tbody></table>
    ${pd.zones.length > 60 ? `<div class="muted small">… ${pd.zones.length - 60} zone(s) de plus</div>` : ""}
    <div class="patch-note ${pd.checksum && pd.checksum.ready && !pd.rsa ? "" : "patch-warn"}">${esc((pd.checksum && pd.checksum.note)
      || "Vérifie toujours le fichier patché avant de le flasher.")}
      ${pd.checksum && !(pd.checksum.ready && !pd.rsa)
        ? " — pas « prêt à flasher » (checksums inconnus, RSA, ou non applicables)."
        : ""}</div>`;

  rep.querySelectorAll("button[data-zi]").forEach(btn => {
    btn.onclick = () => openZoneMap(+btn.dataset.zi);
  });
}

$("#patch_analyze").onclick = () => analyzePatch($("#patch_file").files[0], $("#patch_sol").value);

$("#patch_autofind").onclick = async () => {
  const f = $("#patch_file").files[0];
  const rep = $("#patch_report");
  if (!f) { rep.innerHTML = `<div class="patch-bad">Choisis d'abord un fichier client.</div>`; return; }
  $("#patch_gen").classList.add("hidden");
  $("#patch_ai").classList.add("hidden");
  rep.innerHTML = `<span class="spin"></span> recherche de la meilleure solution…`;
  try {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("relpath", f.name);
    const d = await (await fetch("/analyze", { method: "POST", body: fd })).json();
    const matches = d.matches || [];
    if (!matches.length) {
      rep.innerHTML = `<div class="patch-bad">Aucune correspondance trouvée pour ce fichier dans la base.</div>`;
      return;
    }
    const size = f.size;
    const body = (d.incoming && d.incoming.body_size) || size;
    const sameSize = matches.filter(m => m.stock_size === size || m.stock_size === body);
    const pool = sameSize.length ? sameSize : matches;
    pool.sort((a, b) => (b.exact === true) - (a.exact === true) || b.score - a.score);
    const best = pool[0];

    let why;
    if (best.exact) why = "fichier identique au stock de cette fiche";
    else if (sameSize.length) why = `même taille · similarité ${Math.round(best.score * 100)}%`;
    else why = `⚠ aucune fiche de même taille — meilleur score ${Math.round(best.score * 100)}% (vérifie la compatibilité)`;

    await initPatch();
    $("#patch_sol").value = String(best.id);
    const note = `Solution auto-sélectionnée : ${best.vehicle_label || best.ecu_version || ("#" + best.id)} — ${why}.`;
    await analyzePatch(f, String(best.id), note);
  } catch (e) {
    rep.innerHTML = `<div class="patch-bad">Erreur : ${esc(e.message)}</div>`;
  }
};

async function analyzePatch(f, id, autoNote) {
  const rep = $("#patch_report");
  $("#patch_gen").classList.add("hidden");
  $("#patch_ai").classList.add("hidden");
  window.patchData = null;
  window.patchAutoNote = autoNote || "";
  if (!f) { rep.innerHTML = `<div class="patch-bad">Choisis un fichier client.</div>`; return; }
  if (!id) { rep.innerHTML = `<div class="patch-bad">Aucune solution sélectionnée.</div>`; return; }
  rep.innerHTML = `<span class="spin"></span> analyse…`;
  try {
    const fd = new FormData();
    fd.append("file", f);
    const multi = String(id).includes(",");
    fd.append(multi ? "ids" : "id", id);
    const r = await fetch(multi ? "/patch/analyze_multi" : "/patch/analyze", { method: "POST", body: fd });
    const d = await r.json();
    if (d.error === "original_missing") {
      rep.innerHTML = `<div class="patch-bad">Le fichier <b>original</b> de cette solution est introuvable.
        Va dans Solutions → « Réparer les liens », ou réimporte la fiche.</div>`;
      return;
    }
    if (d.error === "solution_missing") {
      rep.innerHTML = `<div class="patch-bad">Le fichier <b>solution</b> de cette fiche est introuvable sur le disque.</div>`;
      return;
    }
    if (d.error === "taille_incompatible_orig_sol") {
      rep.innerHTML = `<div class="patch-bad">Incompatible : original et solution n'ont pas la même taille
        (${esc(d.detail || "")}). Patch positionnel impossible.</div>`;
      return;
    }
    if (d.error === "taille_incompatible_client") {
      rep.innerHTML = `<div class="patch-bad">Incompatible : ${esc(d.detail || "taille client ≠ original")}.
        Même avec retrait du header programmeur, les corps ne collent pas.</div>`;
      return;
    }
    if (d.error === "solution_identique_original") {
      rep.innerHTML = `<div class="patch-warn">Cette solution est identique à son original : aucune modification à appliquer.</div>`;
      return;
    }
    if (d.error === "stocks_melanges") {
      const g = (d.groups || []).map(x => `${x.types} (${x.n})`).join(" · ");
      rep.innerHTML = `<div class="patch-bad">Stocks / calibres différents — combinaison refusée.
        ${esc(g)}. Décoche les fiches qui ne sont pas le même SW.</div>`;
      return;
    }
    if (d.error) {
      rep.innerHTML = `<div class="patch-bad">${esc(d.detail || d.error)}</div>`;
      return;
    }
    window.patchData = {
      ...d,
      clientFile: f,
      solutionId: (d.ids && d.ids.length) ? d.ids.join(",") : id,
      autoNote: window.patchAutoNote || "",
      clientName: d.clientName || f.name,
      aiLabels: null,
    };
    renderPatchReport();
    $("#patch_gen").classList.remove("hidden");
    $("#patch_partial_wrap").classList.toggle("hidden", d.allMatched);
    $("#patch_partial").checked = false;
    $("#patch_generate").disabled = false;
    $("#patch_ai").classList.remove("hidden");
    $("#patch_ai_run").disabled = !$("#patch_ai_toggle").checked;
    $("#patch_ai_status").textContent = "";
  } catch (e) {
    rep.innerHTML = `<div class="patch-bad">Erreur : ${esc(e.message)}</div>`;
  }
}

$("#patch_ai_toggle").addEventListener("change", () => {
  $("#patch_ai_run").disabled = !($("#patch_ai_toggle").checked && window.patchData);
});

$("#patch_ai_run").onclick = async () => {
  const pd = window.patchData;
  if (!pd) return;
  $("#patch_ai_run").disabled = true;
  $("#patch_ai_status").innerHTML = `<span class="spin"></span> l'IA analyse le résumé…`;
  const summary = {
    plateforme_ecu: pd.fiche.platform || null,
    fabricant: pd.fiche.manufacturer || null,
    type_solution: pd.fiche.solution_type || null,
    taille_fichier: pd.fiche.size,
    zones: pd.zones.map(z => ({
      i: z.i, offset_hex: "0x" + z.off.toString(16), taille: z.len, bits: z.feat.bits,
      forme: z.feat.shape, changement: z.feat.change,
      plage_origine: z.feat.origRange, plage_solution: z.feat.solRange,
      inversions: z.feat.signChanges, lissite: z.feat.smooth,
    })),
  };
  try {
    const r = await fetch("/zones/label", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(summary),
    });
    const d = await r.json();
    if (!d.online) {
      $("#patch_ai_status").textContent = d.error || "IA indisponible.";
    } else if (!d.labels || !d.labels.length) {
      $("#patch_ai_status").textContent = "L'IA n'a pas renvoyé d'étiquettes exploitables.";
    } else {
      pd.aiLabels = d.labels;
      renderPatchReport();
      $("#patch_ai_status").textContent = `IA : ${d.labels.length} zone(s) étiquetée(s).`;
    }
  } catch (e) {
    $("#patch_ai_status").textContent = "Erreur : " + e.message;
  }
  $("#patch_ai_run").disabled = !$("#patch_ai_toggle").checked;
};

$("#patch_generate").onclick = async () => {
  const pd = window.patchData;
  if (!pd || !pd.clientFile) return;
  const partial = $("#patch_partial").checked;
  if (!pd.allMatched && !partial && pd.verdict !== "conflit") {
    alert("Certaines zones ne correspondent pas. Coche « Forcer » pour n'appliquer que les zones compatibles, "
      + "ou choisis une autre solution.");
    return;
  }
  if (pd.verdict === "conflit" && !partial) {
    if (!confirm("Des prestas se recouvrent. Le combinateur n'écrira pas les octets en conflit. Continuer ?"))
      return;
  }
  const fd = new FormData();
  fd.append("file", pd.clientFile);
  const multi = String(pd.solutionId).includes(",");
  fd.append(multi ? "ids" : "id", pd.solutionId);
  fd.append("partial", partial ? "1" : "0");
  const dosId = ($("#patch_dossier") || {}).value;
  if (dosId) fd.append("dossier_id", dosId);
  try {
    const r = await fetch(multi ? "/patch/apply_multi" : "/patch/apply", { method: "POST", body: fd });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert(err.error || "Patch impossible.");
      return;
    }
    const blob = await r.blob();
    const name = pd.clientName || "fichier.bin";
    const dot = name.lastIndexOf(".");
    const base = dot > 0 ? name.slice(0, dot) : name;
    const ext = dot > 0 ? name.slice(dot) : ".bin";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${base}_PATCHED${ext}`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    const ck = (pd.checksum && pd.checksum.note) || "Vérifie les checksums avant de flasher.";
    const ready = pd.checksum && pd.checksum.ready && !pd.rsa;
    alert(`Fichier patché généré : ${base}_PATCHED${ext}\n\n${ck}`
      + (ready ? "" : "\n\n⚠ Pas « prêt à flasher » : checksums inconnus, RSA/CSA, ou à revoir dans WinOLS."));

  } catch (e) {
    alert("Erreur : " + e.message);
  }
};

$("#patch_pack").onclick = async () => {
  const pd = window.patchData;
  if (!pd || !pd.clientFile) return;
  const partial = $("#patch_partial").checked;
  if (!pd.allMatched && !partial && pd.verdict !== "conflit" && pd.verdict !== "propre") {
    alert("Analyse incompatible. Coche « Forcer » ou change de fiche.");
    return;
  }
  if (pd.verdict === "conflit" && !confirm("Des prestas se recouvrent. Le pack n'écrira pas les octets en conflit. Continuer ?"))
    return;
  const fd = new FormData();
  fd.append("file", pd.clientFile);
  const multi = String(pd.solutionId).includes(",");
  fd.append(multi ? "ids" : "id", pd.solutionId);
  fd.append("partial", partial ? "1" : "0");
  const dosId = ($("#patch_dossier") || {}).value;
  if (dosId) fd.append("dossier_id", dosId);
  try {
    const r = await fetch("/patch/pack", { method: "POST", body: fd });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert(err.error || "Pack impossible.");
      return;
    }
    const blob = await r.blob();
    const dispo = r.headers.get("Content-Disposition") || "";
    const m = /filename=\"?([^\";]+)\"?/i.exec(dispo);
    const zipName = m ? m[1] : "pack.zip";
    const ready = pd.checksum && pd.checksum.ready && !pd.rsa && pd.verdict !== "conflit";
    await afterPackResponse(r, blob, zipName, ready);
  } catch (e) {
    alert("Erreur : " + e.message);
  }
};

async function afterPackResponse(r, blob, zipName, ready) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = zipName;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  const dir = r.headers.get("X-Pack-Dir") || "";
  const did = r.headers.get("X-Dossier-Id") || "";
  let msg = "Pack téléchargé : " + zipName;
  if (dir) msg += "\nDossier : " + dir;
  if (!ready) msg += "\n\n⚠ Pas « prêt à flasher ».";
  if (did) {
    if (confirm(msg + "\n\nMarquer le dossier #" + did + " comme Livré et ouvrir le dossier de livraison ?")) {
      await fetch("/dossiers/" + did + "/livre", { method: "POST" });
      if (dir) await fetch("/reveal", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: dir }),
      });
      window.dossierId = +did;
      window.openingDos = true;
      showView("jobs");
      if (typeof openDossier === "function") openDossier(+did);
      window.openingDos = false;
    }
  } else {
    alert(msg);
    if (dir && confirm("Ouvrir le dossier de livraison ?")) {
      await fetch("/reveal", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: dir }),
      });
    }
  }
}

/* ===================== Réglages (clé API) ===================== */
async function refreshSettingsStatus() {
  try {
    const d = await (await fetch("/settings")).json();
    const el = $("#set_status");
    if (d.has_key) {
      const src = d.source === "ui" ? "saisie dans l'app" : "variable d'environnement";
      el.innerHTML = `🟢 Clé active (${src}) : <code>${esc(d.masked)}</code>`;
    } else {
      el.innerHTML = "⚪ Aucune clé — l'étiquetage IA est désactivé (le mode local fonctionne).";
    }
    $("#pw_status").innerHTML = d.has_password
      ? "🔒 Verrou actif — un mot de passe est requis pour accéder à l'outil."
      : "🔓 Pas de verrou — accès libre à qui est sur le réseau.";
    $("#logoutLink").classList.toggle("hidden", !d.has_password && !MOI);
  } catch (e) { $("#set_status").textContent = ""; }
}
$("#settingsBtn").onclick = () => {
  $("#set_key").value = "";
  $("#set_pw").value = "";
  $("#settingsModal").classList.remove("hidden");
  refreshSettingsStatus();
  loadPortalConfig();
};
$("#set_cancel").onclick = () => $("#settingsModal").classList.add("hidden");
$("#pw_save").onclick = async () => {
  const pw = $("#set_pw").value;
  if (pw && pw.length < 4) { $("#pw_status").textContent = "⚠ Mot de passe trop court (4 caractères minimum)."; return; }
  await fetch("/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_password: pw }),
  });
  $("#set_pw").value = "";
  await refreshSettingsStatus();
};

async function loadPortalConfig() {
  try {
    const d = await (await fetch("/portal-config")).json();
    $("#pc_shop").value = d.shop_name || "";
    $("#pc_intro").value = d.intro || "";
    $("#pc_contact").value = d.contact || "";
    $("#pc_ask_contact").checked = !!d.ask_contact;
    $("#pc_show_prices").checked = !!d.show_prices;
    $("#pc_currency").value = d.currency || "€";
    $("#pc_default_price").value = (d.default_price ?? "") === null ? "" : (d.default_price ?? "");
    $("#pc_prices").value = d.prices_text || "";
    $("#pc_default_delay").value = d.default_delay || "";
    $("#pc_delays").value = d.delays_text || "";
    $("#pc_password").value = "";
    $("#pc_pw_status").innerHTML = d.has_password
      ? "🔒 Portail verrouillé — les clients doivent connaître le mot de passe."
      : "🔓 Portail public — accès libre, comme un portail client normal.";
  } catch (e) { /* portail non configuré : valeurs par défaut */ }
}

$("#pc_save").onclick = async () => {
  const body = {
    shop_name: $("#pc_shop").value,
    intro: $("#pc_intro").value,
    contact: $("#pc_contact").value,
    ask_contact: $("#pc_ask_contact").checked,
    show_prices: $("#pc_show_prices").checked,
    currency: $("#pc_currency").value,
    default_price: $("#pc_default_price").value,
    prices_text: $("#pc_prices").value,
    default_delay: $("#pc_default_delay").value,
    delays_text: $("#pc_delays").value,
  };
  await fetch("/portal-config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("#pc_status").textContent = "✓ Portail mis à jour — visible immédiatement côté client.";
};
$("#pc_pw_save").onclick = async () => {
  const pw = $("#pc_password").value;
  if (pw && pw.length < 4) { $("#pc_pw_status").textContent = "⚠ Mot de passe trop court (4 caractères minimum)."; return; }
  await fetch("/portal-config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_password: pw }),
  });
  $("#pc_password").value = "";
  await loadPortalConfig();
};
$("#set_save").onclick = async () => {
  await fetch("/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: $("#set_key").value }),
  });
  $("#set_key").value = "";
  await refreshSettingsStatus();
  $("#set_status").innerHTML += " · enregistrée";
};
$("#set_test").onclick = async () => {
  // sauvegarde d'abord si une clé est saisie, puis teste
  if ($("#set_key").value.trim()) {
    await fetch("/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: $("#set_key").value }),
    });
    $("#set_key").value = "";
  }
  $("#set_status").innerHTML = `<span class="spin"></span> test en cours…`;
  try {
    const d = await (await fetch("/settings/test", { method: "POST" })).json();
    $("#set_status").innerHTML = d.ok
      ? `🟢 Clé valide (modèle ${esc(d.model)}).`
      : `🔴 ${esc(d.error || "échec")}`;
  } catch (e) { $("#set_status").textContent = "Erreur : " + e.message; }
};

/* ===================== Tableau de bord ===================== */
async function initDash() {
  const all = (await (await fetch("/solutions")).json()).solutions;
  renderDashStats(all);
  loadBackups();
  loadCartosPrefix();
  loadAtelierStatus();
  $("#dashDupes").innerHTML = `<button id="dashDupBtn" class="ghost sm">Détecter les doublons</button>`;
  $("#dashDupBtn").onclick = loadDuplicates;
}

function facetBlock(all, dim, title) {
  const counts = new Map();
  for (const s of all) for (const c of solCategories(s, dim)) counts.set(c, (counts.get(c) || 0) + 1);
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "fr"));
  const max = entries.length ? entries[0][1] : 1;
  const rows = entries.slice(0, 12).map(([c, n]) =>
    `<div class="bar-row"><span class="bar-lbl">${esc(c)}</span>
       <span class="bar"><span class="bar-fill" style="width:${Math.round(n / max * 100)}%"></span></span>
       <span class="bar-n">${n}</span></div>`).join("");
  return `<div class="dash-card"><h4>${esc(title)}</h4>${rows || '<div class="muted">—</div>'}</div>`;
}

function renderDashStats(all) {
  const total = all.length;
  const miss = (pred) => all.filter(pred).length;
  const incomplete = {
    label: miss(s => !s.vehicle_label),
    ecu: miss(s => !s.ecu_version),
    orig: miss(s => !s.original_file),
    sol: miss(s => !s.solution_file),
  };
  $("#dashStats").innerHTML = `
    <div class="dash-total">${total} <span>fiche(s) en base</span></div>
    <div class="dash-cards">
      ${facetBlock(all, "solution_type", "Par type")}
      ${facetBlock(all, "manufacturer", "Par fabricant")}
      ${facetBlock(all, "platform", "Par plateforme")}
      ${facetBlock(all, "status", "Par statut")}
    </div>
    <div class="dash-card">
      <h4>Fiches incomplètes</h4>
      <div class="incs">
        <span>Sans libellé : <b>${incomplete.label}</b></span>
        <span>Sans version ECU : <b>${incomplete.ecu}</b></span>
        <span>Sans lien original : <b>${incomplete.orig}</b></span>
        <span>Sans fichier solution : <b>${incomplete.sol}</b></span>
      </div>
    </div>`;
}

$("#dashCsv").onclick = () => { window.location = "/export.csv"; };
$("#dashBackupNow").onclick = async () => {
  const d = await (await fetch("/backup", { method: "POST" })).json();
  if (d.ok) { alert(`Sauvegarde créée (${d.count} fiches).`); loadBackups(); }
  else alert("Rien à sauvegarder (base vide).");
};

async function loadDuplicates() {
  $("#dashDupes").innerHTML = `<span class="spin"></span> recherche…`;
  const d = await (await fetch("/duplicates")).json();
  const groups = d.groups || [];
  if (!groups.length) { $("#dashDupes").innerHTML = `<div class="muted">Aucun doublon (binaire identique) détecté.</div>`; return; }
  $("#dashDupes").innerHTML = groups.map((g, gi) =>
    `<div class="dup-group"><div class="muted small">Groupe ${gi + 1} · ${g.rows.length} fiches · même binaire</div>
      ${g.rows.map(r => `<div class="dup-row" data-id="${r.id}">
        <span>${esc(r.vehicle_label || "(sans libellé)")} <span class="muted">· ${esc(r.solution_type || "")}</span></span>
        <button class="ghost sm danger" data-del="${r.id}">Supprimer</button>
      </div>`).join("")}
    </div>`).join("");
  $("#dashDupes").querySelectorAll("[data-del]").forEach(b => {
    b.onclick = async () => {
      if (!confirm("Supprimer cette fiche ? (le fichier sur le disque n'est pas touché)")) return;
      await fetch("/solutions/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: +b.dataset.del }),
      });
      loadDuplicates();
    };
  });
}

async function loadBackups() {
  const d = await (await fetch("/backups")).json();
  const list = d.backups || [];
  if (!list.length) { $("#dashBackups").innerHTML = `<div class="muted">Aucune sauvegarde pour l'instant.</div>`; return; }
  $("#dashBackups").innerHTML = list.map(b => {
    const dt = new Date(b.mtime * 1000).toLocaleString("fr");
    const ko = (b.size / 1024).toFixed(0);
    return `<div class="bk-row">
      <span>${dt} · <b>${b.count != null ? b.count : "?"}</b> fiches · ${ko} Ko</span>
      <button class="ghost sm" data-restore="${esc(b.name)}">Restaurer</button>
    </div>`;
  }).join("");
  $("#dashBackups").querySelectorAll("[data-restore]").forEach(btn => {
    btn.onclick = async () => {
      if (!confirm("Restaurer cette sauvegarde ? La base actuelle sera d'abord sauvegardée, puis remplacée.")) return;
      const r = await (await fetch("/backups/restore", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: btn.dataset.restore }),
      })).json();
      if (r.ok) { alert(`Base restaurée (${r.count} fiches).`); vizSolLoaded = false; patchSolLoaded = false; initDash(); location.reload(); }
      else alert("Erreur : " + (r.error || "échec"));
    };
  });
}

$("#dashImportDb").onclick = async () => {
  const f = $("#dash_import_db").files[0];
  if (!f) { alert("Choisis un fichier .db (sauvegarde Carto Matcher)."); return; }
  if (!confirm(`Importer « ${f.name} » ? La base actuelle sera d'abord sauvegardée, puis remplacée.`)) return;
  const fd = new FormData();
  fd.append("file", f);
  const btn = $("#dashImportDb");
  btn.disabled = true;
  try {
    const r = await fetch("/backups/import", { method: "POST", body: fd });
    const d = await r.json();
    if (!d.ok) { alert("Import impossible : " + (d.error || r.status)); return; }
    alert(`Base importée : ${d.count} fiches.`);
    vizSolLoaded = false; patchSolLoaded = false;
    location.reload();
  } catch (e) {
    alert("Erreur réseau : " + e.message);
  } finally {
    btn.disabled = false;
  }
};

async function loadCartosPrefix() {
  try {
    const d = await (await fetch("/files/prefix")).json();
    if (d.old_prefix) {
      $("#dashPrefix").textContent = d.old_prefix;
      if (!$("#dash_cartos").value) $("#dash_cartos").placeholder = d.old_prefix;
    }
  } catch (e) { /* ignore */ }
}

function renderRemap(d) {
  if (!d.ok) { $("#dashRemapOut").textContent = d.error || "échec"; return; }
  const ori = `${d.originals_found} / ${d.originals} originaux`;
  const sol = `${d.solutions_found} / ${d.solutions} solutions`;
  const act = d.applied ? `Appliqué (${d.updated} fiches).` : "Test — rien n'est écrit.";
  $("#dashRemapOut").innerHTML =
    `${act} Préfixe actuel : <code>${esc(d.old_prefix || "")}</code><br>`
    + `Fichiers trouvés dans le dossier indiqué : ${ori} · ${sol}.`;
}

async function doRemap(apply) {
  const root = $("#dash_cartos").value.trim();
  if (!root) { alert("Indique le dossier CARTOS de cette machine."); return; }
  if (apply && !confirm("Réécrire tous les chemins vers ce dossier ? La base est d'abord sauvegardée.")) return;
  const d = await (await fetch("/files/remap", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root, apply }),
  })).json();
  renderRemap(d);
  if (d.applied) {
    vizSolLoaded = false; patchSolLoaded = false; loadCartosPrefix();
    if (d.originals_found > 0) {
      if (confirm(`${d.originals_found} original(aux) trouvé(s) sur le disque.\nLancer la file atelier (archive + empreintes v2 + versions ECU) ?`)) {
        await runAtelierSync($("#dashAtelierGo"));
      }
    }
  }
}

$("#dashRemapTest").onclick = () => doRemap(false);
$("#dashRemapApply").onclick = () => doRemap(true);

/* ===================== Rapport PDF d'un patch ===================== */
$("#patch_viz").onclick = visualizeFromPatch;
if ($("#patch_combo2d")) $("#patch_combo2d").onclick = openComboMaps;

async function openComboMaps() {
  const pd = window.patchData;
  if (!pd) return;
  const ids = String(pd.solutionId || "").split(",").map(s => s.trim()).filter(Boolean);
  if (!ids.length) return;
  await initViz();
  $("#viz_type").value = "cartes";
  showView("viz");
  vizUpdateControls();
  $("#viz_cartes_wrap").classList.remove("hidden");
  $("#viz_panels").classList.add("hidden");
  $("#viz_curve_wrap").classList.add("hidden");
  const series = [];
  try {
    series.push({ label: "original", bytes: await fetchBytes(ids[0], "original"), color: VIZ_COLORS[0] });
  } catch (e) {
    alert("Original introuvable.");
    return;
  }
  const labels = (pd.fiches || []).map(f => f.solution_type) ;
  for (let i = 0; i < ids.length; i++) {
    try {
      series.push({
        label: labels[i] || ("SOL " + ids[i]),
        bytes: await fetchBytes(ids[i], "solution"),
        color: VIZ_COLORS[(i + 1) % VIZ_COLORS.length],
      });
    } catch (e) { /* skip */ }
  }
  window.vizSeries = series;
  window.vizNote = "combo " + series.map(s => s.label).join(" + ");
  const fd = new FormData();
  fd.append("id", ids[0]);
  fd.append("which", "original");
  const r = await fetch("/maps/scan", { method: "POST", body: fd });
  const d = await r.json();
  window.vizMaps = d.maps || [];
  vizInfo(`${window.vizMaps.length} carte(s) · ${series.map(s => s.label).join(" / ")}`);
  if (window.vizMaps.length) selectMap(Math.max(0, window.vizMaps.findIndex(m => m.changed)));
  else { renderMapList(); renderMapGrid(null); }
}

$("#patch_pdf").onclick = () => {
  const pd = window.patchData;
  if (!pd) return;
  const aiMap = {};
  if (pd.aiLabels) for (const l of pd.aiLabels) aiMap[l.i] = l;
  const date = new Date().toLocaleString("fr");
  const rows = pd.zones.map(z => `<tr>
    <td>${z.i + 1}</td><td>0x${z.off.toString(16)}</td><td>${z.len}</td>
    <td>${z.ok ? "compatible" : "diffère"}</td><td>${esc(z.localLabel)}</td>
    <td>${aiMap[z.i] ? esc(aiMap[z.i].label) : ""}</td></tr>`).join("");
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Rapport patch</title>
    <style>
      body{font-family:Arial,sans-serif;color:#111;margin:32px;font-size:13px}
      h1{font-size:20px;margin:0 0 4px} h2{font-size:14px;margin:18px 0 6px}
      .muted{color:#666} table{border-collapse:collapse;width:100%;margin-top:6px}
      th,td{border:1px solid #ccc;padding:4px 7px;text-align:left;font-size:12px}
      th{background:#f2f2f2} .verdict{padding:8px 10px;border-radius:6px;margin:8px 0}
      .g{background:#e7f7ee} .w{background:#fdf4e0} .b{background:#fdecea}
    </style></head><body>
    <h1>Rapport d'auto-patch — Carto Matcher</h1>
    <div class="muted">Généré le ${esc(date)}</div>
    <h2>Fichier client</h2><div>${esc(pd.clientName)} · ${(pd.client_size || 0).toLocaleString("fr")} octets</div>
    <h2>Solution appliquée</h2>
    <div>${esc(pd.fiche.solution_type || "—")} · plateforme ${esc(pd.fiche.platform || "—")}
      · fabricant ${esc(pd.fiche.manufacturer || "—")}</div>
    <h2>Verdict</h2><div class="verdict">${esc((pd.verdict_text || pd.verdict || "").replace(/[✅🟡🔴]/g, "").trim())}</div>
    <div>${esc(pd.stats)}</div>
    ${pd.checksum ? `<p>${esc(pd.checksum.note || "")}</p>` : ""}
    <h2>Zones modifiées</h2>
    <table><thead><tr><th>#</th><th>Offset</th><th>Taille</th><th>État</th>
      <th>Type (local)</th><th>Type (IA)</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted" style="margin-top:18px">${esc((pd.checksum && pd.checksum.note)
      || "Vérifier le fichier patché avant flash.")}</p>
    <script>window.onload=function(){window.print();}<\/script>
    </body></html>`;
  const w = window.open("", "_blank");
  if (!w) { alert("Autorise les pop-ups pour générer le rapport."); return; }
  w.document.write(html); w.document.close();
};

/* ===================== Dossiers client ===================== */
window.dossierId = null;
window.dossierPrestaOpts = [];
window.dossierPrestas = [];

const DOS_ST = {
  ouvert: ["Ouvert", "ouvert"],
  en_cours: ["En cours", "en_cours"],
  livre: ["Livré", "livre"],
  archive: ["Archivé", "archive"],
};

async function fillPatchDossiers() {
  const sel = $("#patch_dossier");
  if (!sel) return;
  const cur = sel.value;
  try {
    const d = await (await fetch("/dossiers")).json();
    const rows = d.dossiers || [];
    sel.innerHTML = `<option value="">— aucun —</option>` + rows
      .filter(x => x.status !== "archive")
      .map(x => `<option value="${x.id}">${esc(dosLabel(x))}</option>`).join("");
    if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
    else if (window.dossierId) sel.value = String(window.dossierId);
  } catch (e) { /* silencieux */ }
}

function dosLabel(d) {
  return [d.client_name || d.vehicle_label || ("#" + d.id), d.plate].filter(Boolean).join(" · ");
}

async function refreshDosCount() {
  try {
    const d = await (await fetch("/dossiers")).json();
    const n = (d.counts && d.counts.ouverts) || 0;
    const el = $("#dosCount");
    if (!el) return;
    if (n > 0) { el.textContent = n; el.classList.remove("hidden"); }
    else el.classList.add("hidden");
  } catch (e) { /* */ }
}

async function loadJobs() {
  showDosList();
  const box = $("#jobsList");
  box.innerHTML = `<div class="empty"><span class="spin"></span> Chargement…</div>`;
  const q = ($("#dos_q") && $("#dos_q").value) || "";
  const st = ($("#dos_status") && $("#dos_status").value) || "";
  const d = await (await fetch("/dossiers?q=" + encodeURIComponent(q) + "&status=" + encodeURIComponent(st))).json();
  window.dossierPrestaOpts = d.presta_options || window.dossierPrestaOpts;
  refreshDosCount();
  const rows = d.dossiers || [];
  const c = d.counts || {};
  $("#dosStats").textContent = `${c.total || 0} dossier(s) · ${(c.by_status && c.by_status.ouvert) || 0} ouvert(s) · ${(c.by_status && c.by_status.en_cours) || 0} en cours`;
  if (!rows.length) {
    box.innerHTML = `<div class="empty">Aucun dossier. Clique <b>Nouveau dossier</b> — ou depuis une demande portail / une recherche.</div>`;
  } else {
    box.innerHTML = rows.map(r => {
      const [lbl, cls] = DOS_ST[r.status] || [r.status, ""];
      const sub = [r.vehicle_label, r.ecu_platform, (r.prestas || []).join(" + ")].filter(Boolean).join(" · ");
      const when = r.updated_at ? new Date(r.updated_at * 1000).toLocaleString("fr") : "";
      return `<div class="job-row" data-did="${r.id}">
        <div class="job-main">
          <div class="dos-card-top">
            <div class="job-top"><b>${esc(r.client_name || "(sans nom)")}</b>
              ${r.plate ? `<span class="chip soft">${esc(r.plate)}</span>` : ""}
              <span class="badge ${cls}">${esc(lbl)}</span>
              ${r.dump_present ? `<span class="okmark">dump</span>` : ""}</div>
          </div>
          <div class="job-sub muted">${esc(sub || "—")} ${r.jobs_n ? " · " + r.jobs_n + " patch(s)" : ""}</div>
          <div class="job-sub muted">${esc(when)}</div>
        </div>
        <button class="ghost sm" data-opendos="${r.id}">Ouvrir</button>
      </div>`;
    }).join("");
    box.querySelectorAll("[data-opendos]").forEach(b => {
      b.onclick = () => openDossier(+b.dataset.opendos);
    });
    box.querySelectorAll(".job-row").forEach(row => {
      row.addEventListener("dblclick", () => openDossier(+row.dataset.did));
    });
  }
  const ua = d.unattached_jobs || [];
  const uab = $("#dosUnattached");
  if (!ua.length) { uab.innerHTML = ""; return; }
  uab.innerHTML = `<h3>Patchs non rattachés</h3>` + ua.map(j => {
    const dt = new Date(j.created_at * 1000).toLocaleString("fr");
    return `<div class="job-row">
      <div class="job-main">
        <div class="job-top"><b>${esc(j.client_name || "(fichier ?)")}</b> <span class="muted">· ${dt}</span></div>
        <div class="job-sub muted">${esc(j.solution_label || "")} · ${j.zones || 0} zone(s) · ${esc(j.verdict || "")}</div>
      </div>
      <button class="ghost sm" data-attach="${j.id}">Rattacher…</button>
    </div>`;
  }).join("");
  uab.querySelectorAll("[data-attach]").forEach(b => {
    b.onclick = async () => {
      const id = prompt("N° de dossier (ouvre d'abord le dossier pour le voir) :");
      if (!id) return;
      await fetch("/dossiers/" + id + "/attach_job", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: +b.dataset.attach }),
      });
      loadJobs();
    };
  });
}

function showDosList() {
  $("#dosListWrap").classList.remove("hidden");
  $("#dosDetailWrap").classList.add("hidden");
  window.dossierId = null;
}

function showDosDetail() {
  $("#dosListWrap").classList.add("hidden");
  $("#dosDetailWrap").classList.remove("hidden");
}

function fillDosForm(d) {
  d = d || {};
  $("#dos_name").value = d.client_name || "";
  $("#dos_phone").value = d.phone || "";
  $("#dos_email").value = d.email || "";
  $("#dos_company").value = d.company || "";
  $("#dos_plate").value = d.plate || "";
  $("#dos_vin").value = d.vin || "";
  $("#dos_vehicle").value = d.vehicle_label || "";
  $("#dos_ecu").value = d.ecu_version || "";
  $("#dos_platform").value = d.ecu_platform || "";
  $("#dos_manuf").value = d.manufacturer || "";
  $("#dos_st").value = d.status || "ouvert";
  $("#dos_notes").value = d.notes || "";
  window.dossierPrestas = [];
  (d.prestas || []).forEach(x => String(x).split(/\s*\+\s*/).forEach(p => {
    const t = p.trim();
    if (t && !window.dossierPrestas.includes(t)) window.dossierPrestas.push(t);
  }));
  renderDosPrestas();
  renderDosVin();
  $("#dos_title").textContent = d.id
    ? ("Dossier #" + d.id + (d.client_name ? " · " + d.client_name : ""))
    : "Nouveau dossier";
  const dump = $("#dos_dump");
  if (d.dump_present) {
    dump.innerHTML = `Archivé : <a href="/dossiers/${d.id}/dump">${esc((d.dump_file || "").split(/[\\/]/).pop())}</a>`;
  } else {
    dump.innerHTML = `<span class="muted">Pas encore de dump — dépose le fichier client ci-dessous, ou ouvre le dossier depuis une recherche / une demande.</span>`;
  }
  const jobs = d.jobs || [];
  $("#dos_jobs").innerHTML = jobs.length ? jobs.map(j => {
    const dt = new Date(j.created_at * 1000).toLocaleString("fr");
    return `<div class="job-row"><div class="job-main">
      <div class="job-top"><b>${esc(j.client_name || "")}</b> <span class="muted">· ${dt}</span></div>
      <div class="job-sub muted">${esc(j.solution_label || "")} · ${j.zones || 0} zone(s) · ${esc(j.verdict || "")}</div>
    </div></div>`;
  }).join("") : `<div class="muted small">Aucun patch rattaché. Dans Auto-patch, choisis ce dossier avant de générer.</div>`;
  renderDosDup(d.duplicates || []);
}

function renderDosPrestas() {
  const box = $("#dos_presta_chips");
  const opts = window.dossierPrestaOpts || [];
  const on = window.dossierPrestas || [];
  box.innerHTML = opts.map(a =>
    `<button type="button" class="chip ${on.includes(a) ? "hl" : ""}" data-pa="${esc(a)}">${esc(a)}</button>`
  ).join(" ");
  box.querySelectorAll("[data-pa]").forEach(b => {
    b.onclick = () => {
      const a = b.dataset.pa;
      const i = window.dossierPrestas.indexOf(a);
      if (i >= 0) window.dossierPrestas.splice(i, 1);
      else window.dossierPrestas.push(a);
      renderDosPrestas();
    };
  });
}

function renderDosVin() {
  const v = ($("#dos_vin").value || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  const w = $("#dos_vin_warn");
  if (!v) { w.classList.add("hidden"); return; }
  const ok = /^[A-HJ-NPR-Z0-9]{17}$/.test(v);
  w.classList.toggle("hidden", ok);
}

function renderDosDup(hits) {
  const el = $("#dosDup");
  const mine = window.dossierId;
  const list = (hits || []).filter(h => h.id !== mine);
  if (!list.length) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");
  el.innerHTML = `Déjà un dossier : ` + list.map(h =>
    `<button type="button" class="ghost sm" data-opendup="${h.id}">${esc(dosLabel(h))} (${(h.why || []).join(", ")})</button>`
  ).join(" ");
  el.querySelectorAll("[data-opendup]").forEach(b => {
    b.onclick = () => openDossier(+b.dataset.opendup);
  });
}

function readDosForm() {
  return {
    client_name: $("#dos_name").value,
    phone: $("#dos_phone").value,
    email: $("#dos_email").value,
    company: $("#dos_company").value,
    plate: $("#dos_plate").value,
    vin: $("#dos_vin").value,
    vehicle_label: $("#dos_vehicle").value,
    ecu_version: $("#dos_ecu").value,
    ecu_platform: $("#dos_platform").value,
    manufacturer: $("#dos_manuf").value,
    status: $("#dos_st").value,
    notes: $("#dos_notes").value,
    prestas: window.dossierPrestas || [],
  };
}

async function openDossier(id) {
  window.openingDos = true;
  showView("jobs");
  window.openingDos = false;
  showDosDetail();
  if (!id) {
    window.dossierId = null;
    window.dosFromLast = false;
    if (!window.dossierPrestaOpts.length) {
      try {
        const d = await (await fetch("/dossiers")).json();
        window.dossierPrestaOpts = d.presta_options || [];
      } catch (e) { /* */ }
    }
    fillDosForm({});
    return;
  }
  const d = await (await fetch("/dossiers/" + id)).json();
  if (d.error) { alert(d.error); showDosList(); return; }
  window.dossierId = d.id;
  window.dosFromLast = false;
  fillDosForm(d);
}

async function openDossierFromSearch(solId) {
  window.openingDos = true;
  showView("jobs");
  window.openingDos = false;
  showDosDetail();
  window.dossierId = null;
  window.dosFromLast = true;
  const inc = window.lastIncoming || {};
  const nm = inc.name_meta || {};
  if (!window.dossierPrestaOpts.length) {
    try {
      const d = await (await fetch("/dossiers")).json();
      window.dossierPrestaOpts = d.presta_options || [];
    } catch (e) { /* */ }
  }
  fillDosForm({
    ecu_version: inc.best_ecu_version || "",
    ecu_platform: inc.platform || "",
    manufacturer: inc.manufacturer || "",
    vehicle_label: [nm.brand, nm.vehicle].filter(Boolean).join(" "),
    prestas: nm.solution_type ? [nm.solution_type] : [],
    source: "atelier",
  });
  $("#dos_save_st").textContent = "Le dump de la recherche sera archivé à l'enregistrement.";
}

async function openDossierFromInbox(fichier, rec) {
  const body = { fichier };
  if (rec && rec.contact) {
    body.client_name = rec.contact.nom || "";
    body.phone = rec.contact.tel || "";
    body.email = rec.contact.email || "";
    body.plate = rec.contact.immat || "";
    body.vin = rec.contact.vin || "";
    body.vehicle_label = rec.contact.vehicule || "";
  }
  const d = await (await fetch("/dossiers/from_inbox", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();
  if (d.error) { alert(d.error); return; }
  await openDossier(d.id);
}

$("#dos_new") && ($("#dos_new").onclick = () => openDossier(null));
$("#dos_back") && ($("#dos_back").onclick = () => { showDosList(); loadJobs(); });
$("#dos_del") && ($("#dos_del").onclick = async () => {
  if (!window.dossierId) { showDosList(); return; }
  if (!confirm("Supprimer ce dossier ? Les patchs restent dans l'historique non rattaché. Le dump archivé est effacé (pas le fichier d'origine).")) return;
  await fetch("/dossiers/" + window.dossierId + "/delete", { method: "POST" });
  showDosList();
  loadJobs();
});
$("#dos_save") && ($("#dos_save").onclick = async () => {
  const body = readDosForm();
  $("#dos_save_st").textContent = "…";
  try {
    let id = window.dossierId;
    if (!id) {
      body.from_last = !!window.dosFromLast;
      const d = await (await fetch("/dossiers/create", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })).json();
      id = d.id;
      window.dossierId = id;
      window.dosFromLast = false;
    } else {
      await fetch("/dossiers/" + id, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    const fresh = await (await fetch("/dossiers/" + id)).json();
    fillDosForm(fresh);
    $("#dos_save_st").textContent = "Enregistré.";
    fillPatchDossiers();
    refreshDosCount();
  } catch (e) {
    $("#dos_save_st").textContent = "Erreur : " + e.message;
  }
});
$("#dos_vin") && $("#dos_vin").addEventListener("input", renderDosVin);
$("#dos_plate") && $("#dos_plate").addEventListener("blur", async () => {
  const plate = $("#dos_plate").value;
  const vin = $("#dos_vin").value;
  const name = $("#dos_name").value;
  if (!plate && !vin && (name || "").length < 3) return;
  const d = await (await fetch(
    "/dossiers/suggest?plate=" + encodeURIComponent(plate)
    + "&vin=" + encodeURIComponent(vin)
    + "&name=" + encodeURIComponent(name))).json();
  renderDosDup(d.hits || []);
});
let dosTimer = null;
$("#dos_q") && $("#dos_q").addEventListener("input", () => {
  clearTimeout(dosTimer);
  dosTimer = setTimeout(loadJobs, 180);
});
$("#dos_status") && ($("#dos_status").onchange = loadJobs);

$("#dos_dump_up") && ($("#dos_dump_up").onclick = async () => {
  const f = $("#dos_dump_file").files[0];
  if (!f) { alert("Choisis un fichier."); return; }
  if (!window.dossierId) {
    await $("#dos_save").onclick();
  }
  if (!window.dossierId) return;
  const fd = new FormData();
  fd.append("file", f);
  const d = await (await fetch("/dossiers/" + window.dossierId + "/dump", { method: "POST", body: fd })).json();
  if (d.error) { alert(d.error); return; }
  fillDosForm(d.dossier);
});

async function dossierDumpAsFile(id) {
  const r = await fetch("/dossiers/" + id + "/dump");
  if (!r.ok) throw new Error("Pas de dump archivé.");
  const blob = await r.blob();
  let name = "client.bin";
  const cd = r.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename=([^;]+)/i);
  if (m) name = m[1].replace(/["']/g, "").trim();
  return new File([blob], name);
}

$("#dos_to_search") && ($("#dos_to_search").onclick = async () => {
  if (!window.dossierId) { alert("Enregistre d'abord le dossier."); return; }
  try {
    const f = await dossierDumpAsFile(window.dossierId);
    showView("search");
    await upload(f);
  } catch (e) { alert(e.message); }
});
$("#dos_to_patch") && ($("#dos_to_patch").onclick = async () => {
  if (!window.dossierId) { alert("Enregistre d'abord le dossier."); return; }
  try {
    const f = await dossierDumpAsFile(window.dossierId);
    await initPatch();
    $("#patch_dossier").value = String(window.dossierId);
    showView("patch");
    const dt = new DataTransfer();
    dt.items.add(f);
    $("#patch_file").files = dt.files;
    if ($("#patch_sol").value) analyzePatch(f, $("#patch_sol").value);
    else $("#patch_autofind").click();
  } catch (e) { alert(e.message); }
});

$("#patch_new_dos") && ($("#patch_new_dos").onclick = async () => {
  const f = ($("#patch_file").files[0]) || (window.patchData && window.patchData.clientFile);
  showView("jobs");
  await openDossier(null);
  window.dosFromLast = true;
  $("#dos_save_st").textContent = f
    ? "Enregistre : le dump sera archivé si une recherche vient d'être faite, sinon dépose-le ici."
    : "";
});

/* ===================== Demandes clients (portail) ===================== */
const INBOX_VERDICT = { compatible: ["ok", "compatible"], a_verifier: ["warn", "à vérifier"],
                        non_trouve: ["", "non trouvé"] };

async function refreshInboxCount() {
  try {
    const d = await (await fetch("/inbox")).json();
    const el = $("#inboxCount");
    if (d.non_traitees > 0) { el.textContent = d.non_traitees; el.classList.remove("hidden"); }
    else el.classList.add("hidden");
  } catch (e) { /* silencieux */ }
}

async function packInboxFile(name) {
  try {
    const r = await fetch("/inbox/pack", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fichier: name }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert(err.error || err.detail || "Pack inbox impossible.");
      return;
    }
    const blob = await r.blob();
    const dispo = r.headers.get("Content-Disposition") || "";
    const m = /filename=\"?([^\";]+)\"?/i.exec(dispo);
    await afterPackResponse(r, blob, m ? m[1] : "pack.zip", true);
    loadInbox();
  } catch (e) {
    alert("Pack inbox : " + e.message);
  }
}

async function validateInboxCombo(name) {
  const rec = (window.lastInbox || []).find(r => r.fichier === name) || {};
  const want = rec.prestas || [];
  try {
    const blob = await (await fetch("/inbox/file?fichier=" + encodeURIComponent(name))).blob();
    const file = new File([blob], name, { type: "application/octet-stream" });
    window.lastClientFile = file;
    const ana = await (await fetch("/inbox/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fichier: name }),
    })).json();
    const matches = ana.matches || [];
    const ids = [];
    for (const m of matches) {
      const atoms = typeAtomsOf(m);
      const hit = !want.length || want.some(w => atoms.includes(w) || (m.solution_type || "").includes(w));
      if ((m.exact || m.same_stock || m.calibration_exact) && hit) ids.push(m.id);
    }
    if (!ids.length) {
      alert("Pas de fiche même stock pour cette commande. Lance une analyse manuelle.");
      return;
    }
    await autoPatchFromMatch(ids.join(","));
  } catch (e) {
    alert("Combo impossible : " + e.message);
  }
}

async function loadInbox() {
  const box = $("#inboxList");
  box.innerHTML = `<div class="empty"><span class="spin"></span> Chargement…</div>`;
  const d = await (await fetch("/inbox")).json();
  const rows = d.demandes || [];
  window.lastInbox = rows;
  refreshInboxCount();
  if (!rows.length) {
    box.innerHTML = `<div class="empty">Aucune demande pour l'instant. Les fichiers déposés
      sur le portail client apparaîtront ici automatiquement.</div>`;
    return;
  }
  const queue = rows.filter(r => !r.traite && r.present && (r.commande || (r.prestas && r.prestas.length)));
  const qbar = queue.length
    ? `<div class="combo-bar"><b>À packer (${queue.length})</b>
        <button type="button" id="packNext" class="patchbtn">Packer le suivant</button>
        <span class="muted small">${esc((queue[0].prestas || []).join(" + ") || queue[0].fichier)}</span>
      </div>` : "";
  box.innerHTML = qbar + rows.map(r => {
    const [cls, lbl] = INBOX_VERDICT[r.verdict] || ["", r.verdict || "?"];
    const c = r.contact || {};
    const contact = [c.vehicule, c.email, c.tel].filter(Boolean).map(esc).join(" · ");
    const ecu = [r.plateforme, r.fabricant].filter(Boolean).map(esc).join(" / ");
    const size = r.taille ? ` · ${(r.taille / 1024).toFixed(0)} Ko` : "";
    return `<div class="job-row ${r.traite ? "inbox-done" : ""}" data-f="${esc(r.fichier)}">
      <div class="job-main">
        <div class="job-top"><b>${esc(r.fichier)}</b>
          <span class="badge ${cls}">${esc(lbl)}</span>
          ${r.traite ? '<span class="badge ok">traité</span>' : ""}
          ${r.present ? "" : '<span class="badge danger">fichier absent</span>'}</div>
        <div class="job-sub muted">${r.date ? esc(r.date) + " · " : ""}${ecu || "calculateur ?"}${size}</div>
        <div class="job-sub">${contact || '<span class="muted">aucune coordonnée laissée</span>'}</div>
        ${ (r.prestas && r.prestas.length) ? `<div class="job-sub">Commande : <b>${esc((r.prestas||[]).join(" + "))}</b></div>` : "" }
        <div class="inbox-result muted small"></div>
      </div>
      <div class="inbox-actions">
        ${r.present ? `<button class="ghost sm" data-ana="${esc(r.fichier)}">Analyser</button>
        ${(r.prestas && r.prestas.length && !r.traite) ? `<button class="patchbtn sm" data-packinbox="${esc(r.fichier)}">Packer</button>` : ""}
        <button class="ghost sm" data-comboinbox="${esc(r.fichier)}">Valider le combo</button>
        <button class="ghost sm" data-dosinbox="${esc(r.fichier)}">Dossier</button>
        <a class="ghost sm btn-link" href="/inbox/file?fichier=${encodeURIComponent(r.fichier)}">Télécharger</a>` : ""}
        <button class="ghost sm" data-mark="${esc(r.fichier)}" data-to="${r.traite ? 0 : 1}">
          ${r.traite ? "Rouvrir" : "Marquer traité"}</button>
        <button class="ghost sm danger" data-delinbox="${esc(r.fichier)}">Supprimer</button>
      </div>
    </div>`;
  }).join("");

  box.querySelectorAll("[data-dosinbox]").forEach(b => {
    b.onclick = () => openDossierFromInbox(b.dataset.dosinbox);
  });
  box.querySelectorAll("[data-comboinbox]").forEach(b => {
    b.onclick = () => validateInboxCombo(b.dataset.comboinbox);
  });
  box.querySelectorAll("[data-packinbox]").forEach(b => {
    b.onclick = () => packInboxFile(b.dataset.packinbox);
  });
  const pn = $("#packNext");
  if (pn && queue[0]) pn.onclick = () => packInboxFile(queue[0].fichier);
  box.querySelectorAll("[data-ana]").forEach(b => {
    b.onclick = async () => {
      const row = b.closest(".job-row");
      const out = row.querySelector(".inbox-result");
      out.innerHTML = `<span class="spin"></span> analyse…`;
      try {
        const d = await (await fetch("/inbox/analyze", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fichier: b.dataset.ana }),
        })).json();
        if (d.error) { out.innerHTML = esc(d.error); return; }
        const m = d.matches || [];
        if (!m.length) { out.innerHTML = "Aucune solution connue en base pour ce fichier."; return; }
        out.innerHTML = m.slice(0, 3).map(x =>
          `→ <b>${esc(x.vehicle_label || "#" + x.id)}</b> (${esc(x.solution_type || "?")}) — score ${(x.score * 100).toFixed(0)} %${x.exact ? " · exact" : ""}`
        ).join("<br>");
      } catch (e) { out.innerHTML = "Erreur d'analyse."; }
    };
  });
  box.querySelectorAll("[data-mark]").forEach(b => {
    b.onclick = async () => {
      await fetch("/inbox/mark", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fichier: b.dataset.mark, traite: b.dataset.to === "1" }),
      });
      loadInbox();
    };
  });
  box.querySelectorAll("[data-delinbox]").forEach(b => {
    b.onclick = async () => {
      if (!confirm("Supprimer cette demande (et son fichier déposé) ?")) return;
      await fetch("/inbox/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fichier: b.dataset.delinbox }),
      });
      loadInbox();
    };
  });
}

/* ===================== Inspecteur de fichier ===================== */
window.inspStrings = [];
window.inspMeta = null;

$("#insp_run").onclick = async () => {
  const f = $("#insp_file").files[0];
  if (!f) { $("#insp_head").innerHTML = `<div class="patch-bad">Choisis un fichier.</div>`; return; }
  $("#insp_head").innerHTML = `<span class="spin"></span> inspection…`;
  $("#insp_strings").innerHTML = "";
  $("#insp_filter").classList.add("hidden");
  $("#insp_copy").classList.add("hidden");
  try {
    const fd = new FormData(); fd.append("file", f);
    const d = await (await fetch("/inspect", { method: "POST", body: fd })).json();
    if (d.error) { $("#insp_head").innerHTML = `<div class="patch-bad">${esc(d.error)}</div>`; return; }
    const cands = (d.candidates || []).map(c => `${c.value} (${c.type}, ${c.confidence})`);
    window.inspMeta = { d, cands };
    window.inspStrings = d.strings;
    $("#insp_head").innerHTML = `
      <div><b>${esc(d.filename)}</b> · ${d.size.toLocaleString("fr")} octets</div>
      <div>Plateforme : <b>${esc(d.platform || "—")}</b> · Fabricant : <b>${esc(d.manufacturer || "—")}</b></div>
      <div>Identifiants : ${cands.length ? cands.map(esc).join(" · ") : "<span class='muted'>aucun</span>"}</div>
      <div class="muted small">${d.strings.length} chaîne(s) récupérée(s) sur ${d.strings_total} au total`
      + `${d.strings_total > d.strings.length ? " (limité aux " + d.strings.length + " premières)" : ""}`
      + ` · [Z] = isolée par des octets nuls</div>`;
    $("#insp_filter").classList.remove("hidden");
    $("#insp_q").value = ""; $("#insp_zonly").checked = false;
    renderInspStrings();
    $("#insp_copy").classList.remove("hidden");
  } catch (e) {
    $("#insp_head").innerHTML = `<div class="patch-bad">Erreur : ${esc(e.message)}</div>`;
  }
};

function inspFiltered() {
  const q = $("#insp_q").value.trim().toLowerCase();
  const zonly = $("#insp_zonly").checked;
  return window.inspStrings.filter(s => {
    if (zonly && !s.nb) return false;
    if (!q) return true;
    return s.value.toLowerCase().includes(q) || ("0x" + s.offset.toString(16)).includes(q);
  });
}

function renderInspStrings() {
  const list = inspFiltered();
  $("#insp_count").textContent = `${list.length} affichée(s) sur ${window.inspStrings.length}`;
  $("#insp_strings").innerHTML = list.length
    ? list.map(s =>
        `<div class="insp-line"><span class="insp-off">0x${s.offset.toString(16).padStart(6, "0")}</span>`
        + `<span class="insp-z">${s.nb ? "[Z]" : "   "}</span>`
        + `<span class="insp-val">${esc(s.value)}</span></div>`).join("")
    : `<div class="muted">Aucune chaîne pour ce filtre.</div>`;
}

let inspTimer = null;
$("#insp_q").addEventListener("input", () => { clearTimeout(inspTimer); inspTimer = setTimeout(renderInspStrings, 120); });
$("#insp_zonly").addEventListener("change", renderInspStrings);

$("#insp_copy").onclick = async () => {
  const d = window.inspMeta.d, cands = window.inspMeta.cands;
  const list = inspFiltered();
  const lines = [
    `Fichier: ${d.filename}  (${d.size} octets)`,
    `Plateforme: ${d.platform || "-"}   Fabricant: ${d.manufacturer || "-"}`,
    `Identifiants: ${cands.join(" | ") || "(aucun)"}`,
    `Chaines ASCII (${list.length} affichées / ${d.strings_total} au total`
      + `${$("#insp_q").value || $("#insp_zonly").checked ? ", filtrées" : ""}):`,
    ...list.map(s => `0x${s.offset.toString(16).padStart(6, "0")}\t${s.nb ? "Z" : " "}\t${s.value}`),
  ];
  const report = lines.join("\n");
  try {
    await navigator.clipboard.writeText(report);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = report; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  $("#insp_copy").textContent = "Copié ✓";
  setTimeout(() => { $("#insp_copy").textContent = "Copier le rapport"; }, 1500);
};

// Affiche le lien de déconnexion si un verrou d'accès est actif
refreshSettingsStatus();
// Compteur de demandes clients non traitées (badge sur l'onglet),
// rafraîchi automatiquement pour voir arriver les dépôts en direct.
refreshInboxCount();
refreshDosCount();
setInterval(refreshInboxCount, 30000);
setInterval(refreshDosCount, 30000);

/* ===== Clients du fileservice ===== */
const CLIENT_STATUT = { en_attente: ["warn", "en attente"], actif: ["ok", "actif"], bloque: ["danger", "bloqué"] };

async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.error) throw new Error(d.error || "Erreur " + r.status);
  return d;
}

async function refreshClientsCount(rows) {
  try {
    if (!rows) rows = (await (await fetch("/clients")).json()).clients || [];
    const n = rows.filter(c => c.statut === "en_attente").length;
    const el = $("#clientsCount");
    el.textContent = n;
    el.classList.toggle("hidden", !n);
  } catch (e) { /* silencieux */ }
}

async function loadClients() {
  const box = $("#clientsList");
  box.innerHTML = `<div class="empty"><span class="spin"></span> Chargement…</div>`;
  const cd = await (await fetch("/clients")).json();
  const rows = cd.clients || [];
  FS_PACKS = cd.packs || [];
  const NIVEAUX = Object.keys(cd.niveaux || { Standard: 0, Partenaire: 10, VIP: 20 });
  const ncNiv = $("#nc_niveau");
  if (ncNiv && !ncNiv.options.length) ncNiv.innerHTML = NIVEAUX.map(n => `<option>${esc(n)}</option>`).join("");
  refreshClientsCount(rows);
  if (!rows.length) {
    box.innerHTML = `<div class="empty">Aucun client pour l'instant. Les inscriptions faites sur
      l'espace client du portail (/inscription) apparaîtront ici.</div>`;
    return;
  }
  box.innerHTML = rows.map(c => {
    const [cls, lbl] = CLIENT_STATUT[c.statut] || ["", c.statut];
    const actions = c.statut === "en_attente"
      ? `<button class="patchbtn sm" data-cstat="actif" data-id="${c.id}">Valider</button>
         <button class="ghost sm" data-cstat="bloque" data-id="${c.id}">Refuser</button>`
      : c.statut === "actif"
        ? `<button class="ghost sm" data-cstat="bloque" data-id="${c.id}">Bloquer</button>`
        : c.email.endsWith("@invalid") ? `<span class="muted small">supprimé</span>`
        : `<button class="ghost sm" data-cstat="actif" data-id="${c.id}">Débloquer</button>`;
    const suppr = c.email.endsWith("@invalid") ? "" : `<button class="ghost sm" data-cinv="${c.id}" title="Nouveau lien pour choisir son mot de passe (7 jours)">Renvoyer l'invitation</button>
      <button class="ghost sm" data-csuppr="${c.id}" title="Droit à l'effacement (RGPD)">Supprimer le compte</button>`;
    return `<div class="job-row" data-id="${c.id}">
      <div class="job-main">
        <div class="job-top"><b>${esc(c.societe)}</b> <span class="badge ${cls}">${esc(lbl)}</span>
          <span class="badge">${esc(c.niveau)}</span> <span class="muted small">${c.credits} crédits</span></div>
        <div class="job-sub muted">SIRET ${esc(c.siret)}${c.tva ? " · TVA " + esc(c.tva) : ""} · inscrit le ${esc(c.cree_le.slice(0, 10))}</div>
        <div class="job-sub">${[c.contact, c.email, c.tel].filter(Boolean).map(esc).join(" · ")}</div>
        <div class="job-sub patch-row" style="margin-top:6px">
          <label>Crédits <input type="text" class="c-montant" placeholder="+440 ou -59" style="width:100px"></label>
          <input type="text" class="c-libelle" placeholder="motif (ex : pack 400 + 40, virement du 24/09)" style="flex:1">
          <button class="ghost sm" data-ccred="${c.id}">Appliquer</button>
          <select class="c-niveau" data-cniv="${c.id}">
            ${[...new Set([...NIVEAUX, c.niveau])].map(n => `<option ${n === c.niveau ? "selected" : ""}>${esc(n)}</option>`).join("")}
          </select>
        </div>
        <details class="job-sub" style="margin-top:6px">
          <summary class="muted small">Paiement reçu hors ligne (virement, chèque…) : créditer + facturer</summary>
          <div class="patch-row" style="margin-top:6px">
            <select class="c-pack">${FS_PACKS.map((p, i) => `<option value="${i}">${p.label}</option>`).join("")}<option value="">Autre montant…</option></select>
            <label>Crédits <input type="text" class="c-fcred" value="${FS_PACKS[0] ? FS_PACKS[0].credits : ""}" style="width:80px"></label>
            <label>Montant HT (€) <input type="text" class="c-fht" value="${FS_PACKS[0] ? FS_PACKS[0].ht : ""}" style="width:90px"></label>
            <label>Règlement <select class="c-fpay"><option>Virement</option><option>Chèque</option><option>Espèces</option><option>Carte bancaire sur place</option></select></label>
            <label>Référence <input type="text" class="c-fref" placeholder="ex : VIR 24/09 n°123" style="width:150px"></label>
            <button class="patchbtn sm" data-cfac="${c.id}">Créditer et facturer</button>
          </div>
          <div class="c-facs muted small" data-cfacs="${c.id}"></div>
        </details>
        <div class="c-out muted small"></div>
      </div>
      <div class="inbox-actions">${actions}${suppr}</div>
    </div>`;
  }).join("");
}

// Packs de crédits (catalogue.py), fournis par /clients
let FS_PACKS = [];

async function loadClientFactures(cid) {
  const box = document.querySelector(`[data-cfacs="${cid}"]`);
  if (!box) return;
  const d = await (await fetch(`/clients/${cid}/factures`)).json();
  box.innerHTML = d.factures.length ? "Factures : " + d.factures.map(f =>
    `<a class="btn-link" href="/fs/factures/${encodeURIComponent(f.numero)}" target="_blank">${esc(f.numero)}</a> (${f.ttc.toFixed(2)} € TTC)`).join(" · ") : "Aucune facture.";
}

$("#clientsList").addEventListener("toggle", e => {
  if (e.target.tagName === "DETAILS" && e.target.open) {
    const b = e.target.querySelector("[data-cfac]");
    if (b) loadClientFactures(b.dataset.cfac);
  }
}, true);

$("#clientsList").addEventListener("click", async e => {
  const row = e.target.closest(".job-row");
  if (!row) return;
  const out = row.querySelector(".c-out");
  try {
    if (e.target.dataset.cinv) {
      const d = await postJSON("/clients/inviter", { id: +e.target.dataset.cinv });
      alert(d.mail + (d.lien ? "\n\nLien à transmettre au client :\n" + d.lien : ""));
      return;
    }
    if (e.target.dataset.csuppr) {
      const ok = prompt("Supprimer définitivement les données personnelles et les fichiers de ce client ?\n"
        + "Les factures sont conservées (obligation légale de 10 ans). Tape SUPPRIMER pour confirmer.");
      if (ok !== "SUPPRIMER") return;
      const d = await postJSON("/clients/supprimer", { id: +e.target.dataset.csuppr, confirmation: "SUPPRIMER" });
      alert(`Compte anonymisé, fichiers de ${d.demandes} demande(s) supprimés.`);
      await loadClients();
      return;
    }
    if (e.target.dataset.cfac) {
      const cid = e.target.dataset.cfac, i = row.querySelector(".c-pack").value;
      const body = { id: +cid, credits: row.querySelector(".c-fcred").value, ht: row.querySelector(".c-fht").value,
        paiement: row.querySelector(".c-fpay").value, reference: row.querySelector(".c-fref").value,
        designation: i !== "" ? FS_PACKS[+i].designation : "Crédits fileservice" };
      if (!confirm(`Créditer ${body.credits} crédits et émettre une facture de ${body.ht} € HT ?`)) return;
      const d = await postJSON("/clients/facture", body);
      alert(d.nouvelle ? `Facture ${d.numero} émise, crédits ajoutés.` : `Déjà facturé (${d.numero}) : rien n'a été ajouté.`);
      await loadClients();
      return;
    }
    if (e.target.dataset.cstat) {
      const d = await postJSON("/clients/statut", { id: +e.target.dataset.id, statut: e.target.dataset.cstat });
      await loadClients();
      if (d.mail) alert(d.mail);
    } else if (e.target.dataset.ccred) {
      const montant = row.querySelector(".c-montant").value.trim();
      const libelle = row.querySelector(".c-libelle").value.trim();
      if (!montant) { out.textContent = "Indique un nombre de crédits (négatif pour retirer)."; return; }
      if (!confirm(`Appliquer ${montant} crédits à ce client ?`)) return;
      await postJSON("/clients/credits", { id: +e.target.dataset.ccred, montant, libelle });
      await loadClients();
    }
  } catch (err) { out.textContent = err.message; }
});

$("#clientsList").addEventListener("change", async e => {
  if (e.target.classList.contains("c-pack")) {
    const row = e.target.closest(".job-row"), p = FS_PACKS[+e.target.value];
    if (e.target.value !== "" && p) { row.querySelector(".c-fcred").value = p.credits; row.querySelector(".c-fht").value = p.ht; }
    return;
  }
  if (!e.target.dataset.cniv) return;
  try { await postJSON("/clients/niveau", { id: +e.target.dataset.cniv, niveau: e.target.value }); }
  catch (err) { e.target.closest(".job-row").querySelector(".c-out").textContent = err.message; }
});

async function loadSmtp() {
  const d = await (await fetch("/clients/smtp")).json();
  for (const k of ["host", "port", "user", "from", "atelier", "public_url"]) $("#smtp_" + k).value = d[k] || "";
  $("#smtp_password").value = "";
  $("#smtp_password").placeholder = d.password_set ? "inchangé si vide" : "mot de passe de la boîte";
}

$("#smtpSave").onclick = async () => {
  const body = {};
  for (const k of ["host", "port", "user", "password", "from", "atelier", "public_url"]) body[k] = $("#smtp_" + k).value;
  try { await postJSON("/clients/smtp", body); $("#smtpOut").textContent = "Réglages enregistrés."; loadSmtp(); }
  catch (err) { $("#smtpOut").textContent = err.message; }
};

$("#smtpTest").onclick = async () => {
  $("#smtpOut").textContent = "Envoi en cours…";
  try {
    const d = await postJSON("/clients/smtp/test", { to: $("#smtp_test_to").value });
    $("#smtpOut").textContent = d.ok ? "E-mail de test envoyé : vérifie la boîte de réception." : d.error;
  } catch (err) { $("#smtpOut").textContent = err.message; }
};

refreshClientsCount();

/* ===== Fileservice : demandes des clients ===== */
const FS_STATUT = { recu: ["", "reçu"], en_cours: ["en_cours", "en traitement"], attente: ["warn", "info requise"],
                    pret: ["ok", "prêt"], refuse: ["danger", "refusé"] };
const FS_VERDICT = { compatible: ["ok", "solution en base"], a_verifier: ["warn", "à vérifier"], non_trouve: ["", "pas en base"] };
let fsFiltre = "ouverts", fsRows = [], fsSel = null;

function fsDate(s) { return s ? `${s.slice(8, 10)}/${s.slice(5, 7)} ${s.slice(11, 16)}` : ""; }
function fsVeh(d) { const v = d.vehicule || {}; return [v.marque, v.modele].filter(Boolean).join(" ") || "Véhicule ?"; }
function fsEcu(d) { return (d.lecture || {}).ecu || (d.detection || {}).plateforme || "calculateur ?"; }
function fsAuteur() { return $("#fsAuteur").value.trim(); }
try { $("#fsAuteur").value = localStorage.getItem("fs_auteur") || ""; } catch (e) { /* stockage indisponible */ }
$("#fsAuteur").addEventListener("change", () => { try { localStorage.setItem("fs_auteur", fsAuteur()); } catch (e) {} });

async function refreshFsCount() {
  try {
    const d = await (await fetch("/fs/demandes")).json();
    const n = d.stats.counts.recu + d.stats.counts.en_cours
      + d.demandes.filter(x => x.non_lus && x.statut !== "refuse").filter(x => !["recu", "en_cours"].includes(x.statut)).length;
    const el = $("#fsCount");
    el.textContent = n;
    el.classList.toggle("hidden", !n);
    return d;
  } catch (e) { return null; }
}

async function loadFs() {
  const box = $("#fsList");
  box.innerHTML = `<div class="empty"><span class="spin"></span> Chargement…</div>`;
  const d = await refreshFsCount();
  if (!d) { box.innerHTML = `<div class="empty">Erreur de chargement.</div>`; return; }
  const st = d.stats;
  $("#fsStats").textContent = `${st.counts.recu} reçu(s) · ${st.counts.en_cours} en traitement · ${st.counts.attente} en attente client`
    + (st.delai_moyen != null ? ` · délai moyen ${st.delai_moyen} min (30 j)` : "") + ` · ${st.livres_mois} livré(s) ce mois`;
  fsRows = d.demandes;
  renderFsList();
}

function renderFsList() {
  const q = $("#fsSearch").value.trim().toLowerCase();
  const rows = fsRows.filter(d => {
    const okF = fsFiltre === "ouverts" ? ["recu", "en_cours"].includes(d.statut) || (d.non_lus && d.statut !== "refuse")
      : !fsFiltre || d.statut === fsFiltre;
    const txt = [d.numero, d.societe, fsVeh(d), fsEcu(d), (d.vehicule || {}).immat, (d.vehicule || {}).vin].join(" ").toLowerCase();
    return okF && (!q || txt.includes(q));
  });
  // à traiter : les plus anciennes d'abord
  if (fsFiltre === "ouverts") rows.sort((a, b) => a.id - b.id);
  const box = $("#fsList");
  if (!rows.length) { box.innerHTML = `<div class="empty">Aucune demande ici.</div>`; return; }
  box.innerHTML = rows.map(d => {
    const [cls, lbl] = FS_STATUT[d.statut] || ["", d.statut];
    return `<div class="job-row fs-row ${fsSel === d.id ? "sel" : ""}" data-fsid="${d.id}">
      <div class="job-main">
        <div class="job-top"><b>${esc(d.numero)}</b> <span class="badge ${cls}">${esc(lbl)}</span>
          ${d.non_lus ? `<span class="badge warn">${d.non_lus} msg</span>` : ""}</div>
        <div class="job-sub"><b>${esc(fsVeh(d))}</b> · ${esc(fsEcu(d))}</div>
        <div class="job-sub muted">${esc(d.societe)} · ${esc(d.lignes.filter(l => l.credits > 0).map(l => l.nom).join(" + "))} · ${d.total} cr. · ${esc(fsDate(d.cree_le))}</div>
      </div></div>`;
  }).join("");
}

$("#fsFilters").addEventListener("click", e => {
  const b = e.target.closest("[data-fsf]");
  if (!b) return;
  fsFiltre = b.dataset.fsf;
  $("#fsFilters").querySelectorAll("[data-fsf]").forEach(x => x.classList.toggle("active", x === b));
  renderFsList();
});
$("#fsSearch").addEventListener("input", renderFsList);
$("#fsList").addEventListener("click", e => {
  const r = e.target.closest("[data-fsid]");
  if (r) openFs(+r.dataset.fsid);
});

async function openFs(id) {
  fsSel = id;
  renderFsList();
  const box = $("#fsDetail");
  box.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
  const r = await fetch(`/fs/demandes/${id}`);
  const x = await r.json();
  if (x.error) { box.innerHTML = `<div class="empty">${esc(x.error)}</div>`; return; }
  const d = x.demande, v = d.vehicule || {}, l = d.lecture || {}, det = d.detection || {};
  const [cls, lbl] = FS_STATUT[d.statut] || ["", d.statut];
  const [vcls, vlbl] = FS_VERDICT[det.verdict] || ["", det.verdict || "—"];
  const kv = (k, val) => val ? `<dt>${k}</dt><dd>${esc(val)}</dd>` : "";
  const ferme = d.statut === "refuse";
  box.innerHTML = `
    <div class="fs-actions" style="justify-content:space-between">
      <div><h3>${esc(d.numero)} · ${esc(fsVeh(d))} <span class="badge ${cls}">${esc(lbl)}</span></h3>
        <div class="muted small">${esc(d.societe)} · ${esc(d.email)}${d.tel ? " · " + esc(d.tel) : ""} · reçu le ${esc(fsDate(d.cree_le))}</div></div>
    </div>
    <div class="fs-sec" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <dl class="fs-kv">${kv("Moteur", v.moteur)}${kv("Année", v.annee)}${kv("Boîte", v.boite)}${kv("Km", v.km)}${kv("VIN", v.vin)}${kv("Immat.", v.immat)}</dl>
      <dl class="fs-kv">${kv("Outil", l.outil)}${kv("Méthode", l.methode)}${kv("ECU saisi", l.ecu)}${kv("ECU détecté", [det.plateforme, det.fabricant].filter(Boolean).join(" / "))}
        <dt>Bibliothèque</dt><dd><span class="badge ${vcls}">${esc(vlbl)}</span></dd></dl>
    </div>
    <div class="fs-sec">
      <b>${esc(d.lignes.filter(z => z.credits > 0).map(z => z.nom).join(" + "))}</b> — ${d.total} crédits${d.lignes.some(z => z.credits < 0) ? " (" + esc(d.lignes.filter(z => z.credits < 0).map(z => z.nom).join(", ")) + ")" : ""}${d.siege ? " · <b>ouverture au siège</b>" + (d.retour ? " (retour " + esc(d.retour) + ")" : "") : ""}${d.garantie ? " · garantie " + esc(d.garantie === "g2" ? "2 ans" : "1 an") : ""}
      ${d.commentaire ? `<div class="muted" style="white-space:pre-wrap;margin-top:4px">« ${esc(d.commentaire)} »</div>` : ""}
      ${ferme ? `<div class="muted" style="margin-top:4px">Refusé : ${esc(d.motif_refus)}${d.rembourse ? " · remboursé" : ""}</div>` : ""}
    </div>
    <div class="fs-sec fs-actions">
      <a class="ghost sm btn-link" href="/fs/demandes/${d.id}/original">Télécharger l'original</a>
      <button class="ghost sm" data-fsact="analyser">Analyser</button>
      ${ferme ? "" : `<button class="patchbtn sm" data-fsact="unclic" title="Solution même stock en bibliothèque + patch propre + checksums prêts">⚡ Livrer en un clic</button>`}
      <button class="ghost sm" data-fsact="autopatch">Auto-patch</button>
      ${!ferme && d.statut !== "en_cours" ? `<button class="ghost sm" data-fsstat="en_cours">Passer en traitement</button>` : ""}
    </div>
    <div class="fs-ana muted small"></div>
    ${ferme ? "" : `
    <div class="fs-sec">
      <b>Livrer le fichier modifié</b>
      <div class="fs-actions" style="margin-top:6px"><input type="file" id="fsLivFile">
        <input type="text" id="fsLivNote" placeholder="note visible par le client (optionnel)" style="flex:1">
        <button class="patchbtn sm" data-fsact="livrer">Livrer ${x.livrables.length ? "une nouvelle version" : ""}</button></div>
    </div>`}
    ${x.livrables.length ? `<div class="fs-sec"><b>Versions livrées</b><br>${x.livrables.map(z =>
      `<a class="fs-link" href="/fs/demandes/${d.id}/livre/${z.version}">v${z.version} · ${esc(z.nom)}</a>
       <span class="muted small">${esc(fsDate(z.cree_le))}${z.note ? " · " + esc(z.note) : ""}</span>`).join("<br>")}
       ${d.telecharge_le ? `<div class="muted small">Téléchargé par le client le ${esc(fsDate(d.telecharge_le))}</div>` : ""}</div>` : ""}
    <div class="fs-sec">
      <b>Conversation</b>
      <div class="fs-thread">${x.messages.length ? x.messages.map(m => `<div class="fs-msg ${m.auteur}">`
        + `<div class="fs-meta">${m.auteur === "atelier" ? "Atelier" + (m.auteur_nom ? " · " + esc(m.auteur_nom) : "") : esc(m.auteur_nom || "Client")} · ${esc(fsDate(m.cree_le))}</div>`
        + `<div class="fs-txt">${esc(m.texte)}</div>`
        + (m.pj_fichier ? `<a class="fs-link" href="/fs/demandes/${d.id}/pj/${m.id}">📎 ${esc(m.pj_nom)}</a>` : "") + `</div>`).join("")
        : `<span class="muted small">Aucun message.</span>`}</div>
      ${ferme ? "" : `<textarea id="fsMsg" placeholder="Écrire au client…"></textarea>
      <div class="fs-actions" style="margin-top:6px"><input type="file" id="fsMsgPj">
        <label class="muted small"><input type="checkbox" id="fsMsgAttente"> demander une info (passe en « info requise »)</label>
        <button class="patchbtn sm" data-fsact="message">Envoyer</button></div>`}
    </div>
    ${ferme || d.livre_le ? "" : `
    <div class="fs-sec fs-actions">
      <input type="text" id="fsMotif" placeholder="motif du refus (affiché au client)" style="flex:1">
      <button class="ghost sm" data-fsact="refuser">Refuser et rembourser ${d.total} cr.</button>
    </div>`}
    <div class="fs-out muted small" style="margin-top:8px"></div>`;
}

async function fsPostForm(url, fd) {
  const r = await fetch(url, { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.error) throw new Error(d.error || "Erreur " + r.status);
  return d;
}

$("#fsDetail").addEventListener("click", async e => {
  const b = e.target.closest("[data-fsact],[data-fsstat]");
  if (!b || !fsSel) return;
  const out = $("#fsDetail .fs-out");
  const id = fsSel;
  try {
    if (b.dataset.fsstat) {
      await postJSON(`/fs/demandes/${id}/statut`, { statut: b.dataset.fsstat });
    } else if (b.dataset.fsact === "analyser") {
      const ana = $("#fsDetail .fs-ana");
      ana.innerHTML = `<span class="spin"></span> analyse…`;
      const d = await postJSON(`/fs/demandes/${id}/analyser`, {});
      const m = d.matches || [];
      ana.innerHTML = m.length ? m.slice(0, 5).map(x =>
        `→ <b>${esc(x.vehicle_label || "#" + x.id)}</b> (${esc(x.solution_type || "?")}) — score ${(x.score * 100).toFixed(0)} %${x.exact ? " · exact" : ""}${x.same_stock ? " · même stock" : ""}`
      ).join("<br>") : "Aucune solution connue en base pour ce fichier.";
      return;
    } else if (b.dataset.fsact === "unclic") {
      const ana = $("#fsDetail .fs-ana");
      ana.innerHTML = `<span class="spin"></span> préparation…`;
      const p = await postJSON(`/fs/demandes/${id}/preparer`, {});
      if (!p.ok) { ana.innerHTML = `<span class="badge warn">traitement manuel</span> ${esc(p.raison)}`; return; }
      const cr = p.compte_rendu;
      ana.innerHTML = `<span class="badge ok">prêt</span> ${esc(cr.types.join(" + "))} · fiche(s) : ${esc(cr.fiches.join(", "))}
        · checksum : ${esc(cr.checksum || "OK")}${cr.checksum_note ? " — " + esc(cr.checksum_note) : ""}`;
      if (!confirm(`Livrer maintenant au client ?\n\n${cr.types.join(" + ")}\nFiche(s) : ${cr.fiches.join(", ")}\nChecksum : ${cr.checksum || "OK"}\n\nLe client est prévenu par e-mail.`)) return;
      const d = await postJSON(`/fs/demandes/${id}/livrer-auto`, { auteur: fsAuteur() });
      alert(`Version ${d.version} livrée. ${d.mail || ""}`);
    } else if (b.dataset.fsact === "autopatch") {
      const row = fsRows.find(r => r.id === id) || {};
      const blob = await (await fetch(`/fs/demandes/${id}/original`)).blob();
      window.lastClientFile = new File([blob], row.fichier_nom || "original.bin", { type: "application/octet-stream" });
      const d = await postJSON(`/fs/demandes/${id}/analyser`, {});
      const ids = (d.matches || []).filter(x => x.exact || x.same_stock || x.calibration_exact).map(x => x.id);
      if (!ids.length) { out.textContent = "Pas de fiche même stock en base : traitement manuel."; return; }
      await autoPatchFromMatch(ids.join(","));
      return;
    } else if (b.dataset.fsact === "livrer") {
      const f = $("#fsLivFile").files[0];
      if (!f) { out.textContent = "Choisis le fichier modifié."; return; }
      if (!confirm(`Livrer ${f.name} au client ? Il sera prévenu par e-mail.`)) return;
      const fd = new FormData();
      fd.append("file", f); fd.append("note", $("#fsLivNote").value);
      const d = await fsPostForm(`/fs/demandes/${id}/livrer`, fd);
      alert(`Version ${d.version} livrée. ${d.mail || ""}`);
    } else if (b.dataset.fsact === "message") {
      const fd = new FormData();
      fd.append("texte", $("#fsMsg").value); fd.append("auteur", fsAuteur());
      if ($("#fsMsgPj").files[0]) fd.append("pj", $("#fsMsgPj").files[0]);
      if ($("#fsMsgAttente").checked) fd.append("attente", "1");
      const d = await fsPostForm(`/fs/demandes/${id}/message`, fd);
      out.textContent = d.mail || "";
    } else if (b.dataset.fsact === "refuser") {
      const motif = $("#fsMotif").value.trim();
      if (!motif) { out.textContent = "Indique le motif du refus."; return; }
      if (!confirm("Refuser la demande et rembourser le client ?")) return;
      const d = await postJSON(`/fs/demandes/${id}/refuser`, { motif });
      alert(d.mail || "Demande refusée.");
    }
    await loadFs();
    await openFs(id);
  } catch (err) { out.textContent = err.message; }
});

/* Réglages du fileservice */
const FS_SOCIETE = [["raison_sociale", "Raison sociale"], ["forme", "Forme (SAS, SARL…)"], ["capital", "Capital"],
  ["adresse", "Adresse"], ["code_postal", "Code postal"], ["ville", "Ville"], ["siret", "SIRET"], ["rcs", "RCS (ex : RCS Lyon 123 456 789)"],
  ["tva", "N° TVA"], ["email", "E-mail de contact"], ["tel", "Téléphone"],
  ["directeur_publication", "Directeur de la publication"], ["hebergeur", "Hébergeur (nom, adresse, téléphone)"]];
const FS_JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];

let fsPortail = "";
async function loadFsReglages() {
  const d = await (await fetch("/fs/reglages")).json();
  // Adresse du portail : l'adresse publique si renseignée, sinon le portail local (port 5001)
  fsPortail = (d.public_url || `${location.protocol}//${location.hostname}:5001`).replace(/\/$/, "");
  $("#fsSociete").innerHTML = FS_SOCIETE.map(([k, lbl]) =>
    `<label>${lbl} <input type="text" data-soc="${k}" value="${esc(d.societe[k] || "")}"></label>`).join("");
  $("#fsHoraires").innerHTML = FS_JOURS.map((j, i) => {
    const h = d.horaires[String(i)];
    return `<label>${j} <span><input type="number" min="0" max="24" data-hj="${i}" data-hk="0" value="${h ? h[0] : ""}" style="width:64px"> –
      <input type="number" min="0" max="24" data-hj="${i}" data-hk="1" value="${h ? h[1] : ""}" style="width:64px"> h</span></label>`;
  }).join("");
  $("#fsPages").innerHTML = Object.entries(d.pages).map(([k, p]) => `<details class="fs-page">
    <summary><b>${esc(p.titre)}</b> ${p.a_completer ? '<span class="badge warn">informations à compléter</span>' : '<span class="badge ok">complète</span>'}
      · <a class="fs-link" href="${esc(fsPortail)}/legal/${k}" target="_blank">voir la page</a> <span class="muted small">(portail)</span></summary>
    <textarea data-page="${k}" rows="14" style="margin-top:6px;font-family:var(--mono);font-size:12px">${esc(p.texte)}</textarea>
  </details>`).join("");
  $("#fsAutoLivraison").checked = !!d.livraison_auto;
  $("#fsApiActive").checked = !!d.api_active;
  $("#rlSolde").checked = !!d.relances.solde_bas; $("#rlSeuil").value = d.relances.seuil;
  $("#rlDl").checked = !!d.relances.non_telecharge; $("#rlDelai").value = d.relances.delai_h;
  $("#fsRemises").innerHTML = Object.entries(d.remises).map(([n, v]) =>
    `<label>${esc(n)} <span><input type="text" data-remise="${esc(n)}" value="${v}" style="width:60px"> %</span></label>`).join("")
    + `<label>Nouveau niveau <span><input type="text" id="fsNiveauNom" placeholder="ex : Revendeur" style="width:110px">
       <input type="text" id="fsNiveauPct" placeholder="%" style="width:50px"></span></label>`;
  const st = d.stripe;
  $("#fsStripeKey").value = ""; $("#fsStripeWh").value = "";
  $("#fsStripeKey").placeholder = st.secret_key_set ? "enregistrée (vide = inchangée)" : "sk_live_… ou sk_test_…";
  $("#fsStripeWh").placeholder = st.webhook_secret_set ? "enregistré (vide = inchangé)" : "whsec_…";
  $("#fsStripeEtat").textContent = st.secret_key_set ? `Paiement en ligne actif (${st.mode === "test" ? "mode test" : "mode réel"})${st.webhook_secret_set ? "" : " · webhook non configuré"}` : "Paiement en ligne désactivé";
  $("#fsWebhookUrl").textContent = (d.public_url || "https://portail.ton-domaine.fr") + "/stripe/webhook";
}

async function loadFsBackups(post) {
  try {
    const d = post ? await postJSON("/fs/sauvegarde", {}) : await (await fetch("/fs/sauvegarde")).json();
    $("#fsFilesDir").textContent = d.dossier_fichiers;
    const b = d.sauvegardes || [];
    $("#fsBackupOut").textContent = b.length
      ? `Dernière : ${b[0].nom.slice(12, 20).replace(/(\d{4})(\d{2})(\d{2})/, "$3/$2/$1")} ${b[0].nom.slice(21, 23)}h${b[0].nom.slice(23, 25)} · ${b.length} récente(s)` + (post ? " — sauvegarde faite." : "")
      : "Aucune sauvegarde pour l'instant.";
  } catch (err) { $("#fsBackupOut").textContent = err.message; }
}
$("#fsBackup").onclick = () => loadFsBackups(true);

async function loadFsSynthese() {
  const d = await (await fetch("/fs/synthese")).json();
  const eur = v => v.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  const moisFr = m => new Date(m + "-01T12:00:00").toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
  $("#fsSynthese").innerHTML = (d.mois.length ? `<table class="fs-table"><thead><tr><th>Mois</th><th>Factures</th>
      <th>CA HT</th><th>TVA</th><th>TTC</th><th>Crédits vendus</th><th>Crédits consommés</th></tr></thead><tbody>`
    + d.mois.map(m => `<tr><td>${esc(moisFr(m.mois))}</td><td>${m.factures}</td><td>${eur(m.ht)}</td><td>${eur(m.tva)}</td>
      <td>${eur(m.ttc)}</td><td>${m.credits_vendus}</td><td>${m.credits_consommes}</td></tr>`).join("")
    + `</tbody></table>` : "Aucune vente pour l'instant.")
    + `<div style="margin-top:6px">Crédits en circulation (soldes clients non consommés) : <b>${d.credits_en_circulation}</b></div>`;
}
$("#fsCsv").onclick = () => {
  const q = new URLSearchParams({ debut: $("#fsCsvDebut").value, fin: $("#fsCsvFin").value });
  window.location = "/fs/factures.csv?" + q.toString();
};

$("#fsSaveReglages").onclick = async () => {
  const societe = {};
  document.querySelectorAll("[data-soc]").forEach(i => { societe[i.dataset.soc] = i.value; });
  const horaires = {};
  for (let j = 0; j < 7; j++) {
    const o = document.querySelector(`[data-hj="${j}"][data-hk="0"]`).value, f = document.querySelector(`[data-hj="${j}"][data-hk="1"]`).value;
    horaires[j] = o !== "" && f !== "" ? [+o, +f] : null;
  }
  try {
    const pages = {};
    document.querySelectorAll("[data-page]").forEach(t => { pages[t.dataset.page] = t.value; });
    const remises = {};
    document.querySelectorAll("[data-remise]").forEach(i => { remises[i.dataset.remise] = i.value; });
    if ($("#fsNiveauNom").value.trim()) remises[$("#fsNiveauNom").value.trim()] = $("#fsNiveauPct").value || "0";
    const relances = { solde_bas: $("#rlSolde").checked, seuil: $("#rlSeuil").value,
                       non_telecharge: $("#rlDl").checked, delai_h: $("#rlDelai").value };
    await postJSON("/fs/reglages", { societe, horaires, pages, remises, relances, livraison_auto: $("#fsAutoLivraison").checked, api_active: $("#fsApiActive").checked, stripe: { secret_key: $("#fsStripeKey").value, webhook_secret: $("#fsStripeWh").value } });
    $("#fsReglagesOut").textContent = "Réglages enregistrés.";
    loadFsReglages();
  } catch (err) { $("#fsReglagesOut").textContent = err.message; }
};

refreshFsCount();

/* ===== Alertes en direct : nouveau fichier, message client, inscription ===== */
const ALERTE_INTERVALLE = 20000;
let alerteEtat = null;          // dernier instantané connu
const titreBase = document.title;

function alertesActives() {
  try { return localStorage.getItem("fs_alertes") !== "off"; } catch (e) { return true; }
}
function majBoutonAlertes() {
  const b = $("#fsNotifBtn");
  if (!b) return;
  const perm = ("Notification" in window) ? Notification.permission : "indisponible";
  b.textContent = alertesActives() ? (perm === "granted" ? "🔔 Alertes activées" : "🔔 Alertes (son)") : "🔕 Alertes coupées";
}
$("#fsNotifBtn").onclick = async () => {
  const actif = alertesActives();
  if (actif && "Notification" in window && Notification.permission === "default") {
    await Notification.requestPermission();        // demande seulement sur clic
  } else {
    try { localStorage.setItem("fs_alertes", actif ? "off" : "on"); } catch (e) {}
  }
  bip(); majBoutonAlertes();
};

function bip() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1320].forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
      const t = ctx.currentTime + i * 0.18;
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
      o.start(t); o.stop(t + 0.17);
    });
  } catch (e) { /* pas de son disponible */ }
}

function notifier(titre, texte) {
  if (!alertesActives()) return;
  bip();
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      const n = new Notification(titre, { body: texte, tag: "carto-fs" });
      n.onclick = () => { window.focus(); showView("fs"); n.close(); };
    } catch (e) { /* notifications indisponibles (réseau local sans HTTPS) */ }
  }
}

async function surveiller() {
  let a;
  try { a = await (await fetch("/fs/alertes")).json(); } catch (e) { return; }
  const total = a.a_traiter + a.non_lus + a.inscriptions;
  document.title = total ? `(${total}) ${titreBase}` : titreBase;
  const el = $("#fsCount");
  const nFs = a.a_traiter + a.non_lus;
  el.textContent = nFs; el.classList.toggle("hidden", !nFs);
  const ec = $("#clientsCount");
  ec.textContent = a.inscriptions; ec.classList.toggle("hidden", !a.inscriptions);
  if (alerteEtat) {
    const msgs = [];
    if (a.derniere_demande > alerteEtat.derniere_demande) msgs.push(`${a.derniere_demande - alerteEtat.derniere_demande} nouveau(x) fichier(s)`);
    if (a.dernier_message > alerteEtat.dernier_message) msgs.push("nouveau message client");
    if (a.inscriptions > alerteEtat.inscriptions) msgs.push("nouvelle inscription");
    if (msgs.length) {
      notifier("Fileservice E85-FRANCE", msgs.join(" · "));
      // liste à jour si l'onglet est ouvert (sans perdre la demande sélectionnée)
      if (!$("#view-fs").classList.contains("hidden")) loadFs();
      if (!$("#view-clients").classList.contains("hidden") && a.inscriptions > alerteEtat.inscriptions) loadClients();
    }
  }
  alerteEtat = a;
}
majBoutonAlertes();
surveiller();
setInterval(surveiller, ALERTE_INTERVALLE);

/* ===== Équipe de l'atelier et journal ===== */
let MOI = null;

async function initSession() {
  try {
    const d = await (await fetch("/equipe")).json();
    MOI = d.moi;
    if (MOI) {
      const lk = $("#logoutLink");
      lk.classList.remove("hidden");
      lk.textContent = `${MOI.nom} ⎋`;
      lk.title = `Connecté : ${MOI.nom} (${MOI.role}) — se déconnecter`;
      // signature automatique : plus besoin du champ
      const lab = $("#fsAuteur").closest("label");
      if (lab) lab.innerHTML = `<span class="muted small">Connecté : <b>${esc(MOI.nom)}</b></span>`;
      if (MOI.role !== "admin") {
        ["#fsReglages", "#fsEquipeForm"].forEach(sel => { const el = $(sel); if (el) el.classList.add("hidden"); });
        document.body.classList.add("role-technicien");
      }
    }
  } catch (e) { /* outil sans comptes */ }
}

async function loadEquipe() {
  const d = await (await fetch("/equipe")).json();
  const intro = $("#fsEquipeIntro");
  if (!d.active) {
    intro.innerHTML = "Aucun compte pour l'instant : l'outil est accessible sans identifiant (ou avec le mot de passe unique des Réglages). "
      + "<b>Crée ton compte administrateur</b> : dès lors, chaque technicien se connecte avec son identifiant, ses messages sont signés "
      + "automatiquement et ses actions sont tracées dans le journal.";
    $("#eqRole").value = "admin"; $("#eqRole").disabled = true;
    $("#eqCreer").textContent = "Créer mon compte administrateur";
  } else {
    intro.textContent = MOI && MOI.role !== "admin" ? "Seul un administrateur peut gérer les comptes."
      : "Administrateur : tout, dont crédits, factures, suppressions et réglages. Technicien : traitement des demandes et validation des inscriptions.";
    $("#eqRole").disabled = false;
    $("#eqCreer").textContent = "Créer le compte";
  }
  $("#fsEquipeListe").innerHTML = (d.techniciens || []).map(t => `<div class="job-row" data-tid="${t.id}">
    <div class="job-main"><div class="job-top"><b>${esc(t.nom)}</b> <span class="muted small">${esc(t.identifiant)}</span>
      <span class="badge ${t.role === "admin" ? "ok" : ""}">${t.role === "admin" ? "administrateur" : "technicien"}</span>
      ${t.actif ? "" : '<span class="badge danger">désactivé</span>'}</div>
      <div class="job-sub muted small">Dernière connexion : ${esc(t.derniere_connexion || "jamais")}</div></div>
    <div class="inbox-actions">
      <button class="ghost sm" data-eqrole="${t.role === "admin" ? "technicien" : "admin"}">${t.role === "admin" ? "Passer technicien" : "Passer admin"}</button>
      <button class="ghost sm" data-eqactif="${t.actif ? 0 : 1}">${t.actif ? "Désactiver" : "Réactiver"}</button>
      <button class="ghost sm" data-eqmdp="1">Nouveau mot de passe</button>
    </div></div>`).join("");
  window.fsEquipe = d.techniciens || [];
}

$("#eqCreer").onclick = async () => {
  try {
    const d = await postJSON("/equipe/creer", { identifiant: $("#eqId").value, nom: $("#eqNom").value,
                                                role: $("#eqRole").value, mdp: $("#eqMdp").value });
    $("#eqOut").textContent = d.premier ? "Compte administrateur créé : tu es connecté avec. Crée maintenant les comptes des techniciens."
                                        : "Compte créé.";
    ["#eqId", "#eqNom", "#eqMdp"].forEach(sel => { $(sel).value = ""; });
    await initSession(); loadEquipe(); loadJournal();
  } catch (err) { $("#eqOut").textContent = err.message; }
};

$("#fsEquipeListe").addEventListener("click", async e => {
  const row = e.target.closest("[data-tid]");
  if (!row || e.target.tagName !== "BUTTON") return;
  const t = (window.fsEquipe || []).find(x => x.id === +row.dataset.tid);
  if (!t) return;
  const body = { id: t.id, nom: t.nom, role: t.role, actif: !!t.actif };
  if (e.target.dataset.eqrole) body.role = e.target.dataset.eqrole;
  if (e.target.dataset.eqactif) body.actif = e.target.dataset.eqactif === "1";
  if (e.target.dataset.eqmdp) {
    const mdp = prompt(`Nouveau mot de passe pour ${t.nom} (10 caractères minimum) :`);
    if (!mdp) return;
    body.mdp = mdp;
  }
  try { await postJSON("/equipe/modifier", body); $("#eqOut").textContent = "Compte mis à jour."; loadEquipe(); loadJournal(); }
  catch (err) { $("#eqOut").textContent = err.message; }
});

async function loadJournal() {
  const q = $("#jnQ").value.trim();
  const d = await (await fetch("/equipe/journal?q=" + encodeURIComponent(q))).json();
  $("#jnListe").innerHTML = d.journal.length ? `<table class="fs-table"><thead><tr><th>Date</th><th>Qui</th><th>Action</th><th>Sur</th><th>Détail</th></tr></thead><tbody>`
    + d.journal.map(j => `<tr><td style="white-space:nowrap">${esc(j.date.slice(8, 10) + "/" + j.date.slice(5, 7) + " " + j.date.slice(11, 16))}</td>
      <td>${esc(j.technicien || "—")}</td><td>${esc(j.action)}</td><td>${esc(j.cible)}</td><td>${esc(j.detail)}</td></tr>`).join("")
    + "</tbody></table>" : "Aucune action enregistrée.";
}
$("#jnRefresh").onclick = loadJournal;
$("#jnQ").addEventListener("change", loadJournal);

initSession();

/* ===== Création manuelle d'un compte client ===== */
$("#nc_mdp_moi").addEventListener("change", e => $("#nc_mdp").classList.toggle("hidden", !e.target.checked));
$("#ncCreer").onclick = async () => {
  const body = {};
  ["societe", "email", "pays", "siret", "tva", "contact", "tel", "adresse", "code_postal", "ville", "niveau", "langue", "credits"]
    .forEach(k => { body[k] = $("#nc_" + k).value; });
  if ($("#nc_mdp_moi").checked) body.mdp = $("#nc_mdp").value;
  const out = $("#ncOut");
  try {
    const d = await postJSON("/clients/creer", body);
    out.innerHTML = `<span class="badge ok">compte créé</span> ${esc(d.mail || "")}`
      + (d.lien ? `<br>Lien à transmettre au client : <code>${esc(d.lien)}</code>` : "");
    ["societe", "email", "siret", "tva", "contact", "tel", "adresse", "code_postal", "ville", "credits", "mdp"]
      .forEach(k => { $("#nc_" + k).value = ""; });
    loadClients();
  } catch (err) { out.textContent = err.message; }
};

