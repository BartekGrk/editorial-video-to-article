// Interfejs redaktora: bez frameworka i bez kroku budowania.
// Routing przez hash: #/ (lista), #/new, #/m/<id>, #/m/<id>/edit, #/styles.
"use strict";

const view = document.getElementById("view");
const state = { config: null, material: null, profiles: [], tab: "draft" };

const STATUS = {
  PROCESSING: ["Przetwarzanie…", "warn"],
  FAILED: ["Błąd", "err"],
  NEW: ["Nowy", ""],
  ANALYZED: ["Przeanalizowany", "warn"],
  DRAFTED: ["Draft", "warn"],
  APPROVED: ["Zatwierdzony", "ok"],
};

const STAGES = [
  ["QUEUED", "W kolejce"],
  ["DOWNLOADING", "Pobieranie nagrania"],
  ["TRANSCRIBING", "Transkrypcja mowy"],
  ["SPEAKERS", "Rozpoznanie mówców"],
  ["ANALYZING", "Materiał dowodowy"],
  ["WRITING", "Draft i weryfikacja"],
  ["DONE", "Gotowe"],
];

// --- narzędzia ---------------------------------------------------------------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function tc(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "show" + (isError ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.className = ""), isError ? 6000 : 2500);
}

async function api(path, options = {}) {
  const init = { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = Array.isArray(data.detail)
        ? data.detail.map((d) => (typeof d === "string" ? d : `${(d.loc || []).join(".")}: ${d.msg}`)).join("; ")
        : typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) { /* odpowiedź bez JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

// Blokuje przycisk na czas operacji i pokazuje błąd zamiast cichej porażki.
async function busy(button, label, fn) {
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = label; }
  try { return await fn(); }
  catch (error) { toast(error.message, true); }
  finally { if (button) { button.disabled = false; button.textContent = original; } }
}

function videoLink(url, seconds) {
  if (!url) return null;
  if (/youtube\.com|youtu\.be/.test(url)) return url + (url.includes("?") ? "&" : "?") + "t=" + seconds;
  return url.split("#")[0] + "#t=" + seconds;
}

function youtubeId(url) {
  const match = (url || "").match(/(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([\w-]{6,})/);
  return match ? match[1] : null;
}

function isDirectVideo(url) {
  return /\.(mp4|webm|ogg|m3u8)(\?|#|$)/i.test(url || "");
}

// --- routing -----------------------------------------------------------------

async function route() {
  const hash = location.hash || "#/";
  let match;
  try {
    if (hash === "#/" || hash === "#") return await renderList();
    if (hash === "#/new") return await renderForm(null);
    if (hash === "#/styles") return await renderStyles();
    if ((match = hash.match(/^#\/m\/(\w+)\/edit$/))) return await renderForm(match[1]);
    if ((match = hash.match(/^#\/m\/(\w+)$/))) return await renderWorkspace(match[1]);
    view.innerHTML = `<div class="empty">Nie ma takiej strony. <a href="#/">Wróć do listy</a></div>`;
  } catch (error) {
    view.innerHTML = `<div class="card"><strong>Błąd:</strong> ${esc(error.message)}</div>`;
  }
}

window.addEventListener("hashchange", route);

async function loadConfig() {
  state.config = await api("/api/config");
  const badge = document.getElementById("engine-badge");
  if (state.config.engine === "anthropic") {
    badge.textContent = `AI: ${state.config.model}`;
    badge.className = "badge ok";
  } else {
    badge.textContent = "Tryb demonstracyjny (bez AI)";
    badge.className = "badge warn";
    badge.title = "Ustaw ANTHROPIC_API_KEY, aby generować drafty modelem Claude.";
  }
}

// --- lista materiałów --------------------------------------------------------

async function renderList() {
  const materials = await api("/api/materials");
  const rows = materials.map((m) => {
    const [label, cls] = STATUS[m.status];
    const stage = m.status === "PROCESSING" ? ` · ${esc(stageName(m.job_stage))}` : "";
    return `<tr class="clickable" data-id="${esc(m.id)}">
      <td><strong>${esc(m.title)}</strong><div class="small muted">${esc(m.topic)}${stage}</div></td>
      <td>${esc(m.category)}</td>
      <td>${m.mode ? esc(m.mode) : "–"}</td>
      <td><span class="badge ${cls}">${label}</span></td>
      <td class="small muted">${esc(m.updated_at.replace("T", " ").slice(0, 16))}</td>
    </tr>`;
  }).join("");

  view.innerHTML = `
    <div class="row"><h1>Materiały wideo</h1><span class="spacer"></span>
      <a class="button" href="#/new">+ Z transkrypcji</a></div>
    ${autoForm()}
    <div class="card">
      ${materials.length ? `<table>
        <thead><tr><th>Tytuł</th><th>Kategoria</th><th>Tryb</th><th>Status</th><th>Zmiana</th></tr></thead>
        <tbody>${rows}</tbody></table>`
      : `<div class="empty">Brak materiałów. Dodaj transkrypcję nagrania, aby przygotować draft.<br><br>
          <button id="demo" class="button">Wczytaj przykładowy materiał</button></div>`}
    </div>
    <p class="small muted">Narzędzie przygotowuje weryfikowalne drafty. Decyzję o publikacji i ostateczną redakcję podejmuje człowiek.</p>`;

  view.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/m/${tr.dataset.id}`)));
  bindAutoForm();
  // Lista odświeża się sama, dopóki coś jest przetwarzane.
  clearTimeout(renderList.timer);
  if (materials.some((m) => m.status === "PROCESSING")) {
    renderList.timer = setTimeout(() => { if ((location.hash || "#/") === "#/") route(); }, 4000);
  }
  view.querySelector("#demo")?.addEventListener("click", (e) => busy(e.target, "Wczytywanie…", async () => {
    const created = await api("/api/materials", { method: "POST", body: DEMO_MATERIAL });
    location.hash = `#/m/${created.id}`;
  }));
}

// Syntetyczny przykład (nie pochodzi z realnego nagrania).
const DEMO_MATERIAL = {
  title: "Sonda: czy oddajemy butelki w systemie kaucyjnym?",
  category: "sonda uliczna",
  topic: "System kaucyjny po kilku miesiącach",
  angle: "Czy kaucja motywuje do oddawania opakowań",
  video_url: "",
  external_processing_allowed: true,
  transcript_raw: [
    "[00:00:05] Reporterka: Pytamy przechodniów, czy oddają butelki w systemie kaucyjnym.",
    "[00:00:12] Przechodzień 1: Oddaję, bo automat jest w sklepie obok domu. To naprawdę wygodne.",
    "[00:00:18] Przechodzień 1: Czasem nie działa, ale zwykle trwa to minutę, może dwie.",
    "[00:00:31] Przechodzień 2: Ja nie oddaję. Za te trzydzieści groszy nie będę stać w kolejce.",
    "[00:00:44] Przechodzieńka 3: Słyszałam, że w zeszłym miesiącu oddano miliard butelek, ale nie wiem, czy to prawda.",
    "[00:00:58] Reporterka: A co z puszkami?",
    "[00:01:02] Przechodzieńka 3: Puszki oddaję razem z butelkami, dzieci pilnują, żeby niczego nie wyrzucić.",
  ].join("\n"),
};

// --- automat z nagrania ------------------------------------------------------

function stageName(stage) {
  return (STAGES.find(([key]) => key === stage) || [stage, stage || ""])[1];
}

function autoForm() {
  const asr = state.config?.asr;
  return `
    <form class="card" id="auto-form">
      <h2>Automatycznie z nagrania</h2>
      <p class="small muted">Plik wideo/audio albo link (YouTube lub bezpośredni link do pliku) → transkrypcja → materiał dowodowy →
        draft w stylu WP Finanse → weryfikacja cytatów. Gotowy draft, który przeszedł weryfikację, zostaje zatwierdzony do eksportu.</p>
      ${asr ? "" : '<p class="verify err small">Na serwerze brakuje faster-whisper lub ffmpeg – transkrypcja nagrań jest niedostępna.</p>'}
      <div class="grid2">
        <div><label for="auto-file">Plik nagrania</label><input type="file" id="auto-file" accept="video/*,audio/*"></div>
        <div><label for="auto-url">…albo link</label><input type="url" id="auto-url" placeholder="https://"></div>
        <div><label for="auto-title">Tytuł roboczy (opcjonalnie)</label><input type="text" id="auto-title"></div>
        <div><label for="auto-mode">Tryb</label><select id="auto-mode">
          <option value="">Automatycznie (według materiału)</option><option value="SHORT">SHORT – tekst pod wideo</option><option value="FULL">FULL – pełny artykuł</option></select></div>
      </div>
      <label class="check"><input type="checkbox" id="auto-consent">
        <span>Materiał jest publiczny lub polityka redakcji pozwala wysłać transkrypcję do zewnętrznego modelu językowego.</span></label>
      <label class="check"><input type="checkbox" id="auto-approve" checked>
        <span>Zatwierdź automatycznie, jeśli weryfikacja nie wykryje błędów.</span></label>
      <div class="row" style="margin-top:.8rem"><button class="primary" type="submit" ${asr ? "" : "disabled"}>Uruchom automat</button></div>
    </form>`;
}

function bindAutoForm() {
  const form = view.querySelector("#auto-form");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData();
    const file = form.querySelector("#auto-file").files[0];
    if (file) data.append("file", file);
    data.append("url", form.querySelector("#auto-url").value.trim());
    data.append("title", form.querySelector("#auto-title").value.trim());
    data.append("mode", form.querySelector("#auto-mode").value);
    data.append("external_processing_allowed", form.querySelector("#auto-consent").checked);
    data.append("auto_approve", form.querySelector("#auto-approve").checked);
    busy(event.submitter, file ? "Wysyłanie pliku…" : "Uruchamiam…", async () => {
      const response = await fetch("/api/auto", { method: "POST", body: data });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Nie udało się uruchomić automatu");
      renderWorkspace.lastStage = "PROCESSING";
      location.hash = `#/m/${body.id}`;
    });
  });
}

function drawProcessing(m) {
  const current = STAGES.findIndex(([key]) => key === m.job_stage);
  const failed = m.status === "FAILED";
  view.innerHTML = `
    <div class="row"><h1 style="margin:0">${esc(m.title)}</h1><span class="badge ${failed ? "err" : "warn"}">${failed ? "Błąd" : "Przetwarzanie…"}</span></div>
    <p class="muted small">${esc(m.source)}</p>
    <div class="card">
      <ol class="stages">${STAGES.map(([key, name], i) => {
        const cls = failed && i === Math.max(current, 0) ? "err" : i < current || m.job_stage === "DONE" ? "done" : i === current ? "active" : "";
        return `<li class="${cls}">${name}</li>`;
      }).join("")}</ol>
      ${failed ? `<div class="verify err"><strong>Błąd:</strong> ${esc(m.job_error || "nieznany")}</div>
        <button id="delete-failed">Usuń materiał</button>` : '<p class="small muted">Transkrypcja trwa zwykle około długości nagrania. Strona odświeża się sama.</p>'}
    </div>`;
  view.querySelector("#delete-failed")?.addEventListener("click", (e) => busy(e.target, "Usuwanie…", async () => {
    await api(`/api/materials/${m.id}`, { method: "DELETE" });
    location.hash = "#/";
  }));
}

// --- formularz materiału -----------------------------------------------------

async function renderForm(id) {
  const [material, profiles] = await Promise.all([id ? api(`/api/materials/${id}`) : null, api("/api/style-profiles")]);
  const m = material || { title: "", category: "", topic: "", angle: "", video_url: "", transcript_raw: "", external_processing_allowed: false, style_profile_id: null };
  const categories = ["sonda uliczna", "wywiad", "reportaż", "komentarz eksperta", "konferencja", "inne"];

  view.innerHTML = `
    <h1>${id ? "Edycja materiału" : "Nowy materiał"}</h1>
    <form class="card" id="material-form">
      <div class="grid2">
        <div><label for="title">Tytuł roboczy</label><input type="text" id="title" required value="${esc(m.title)}"></div>
        <div><label for="category">Kategoria</label>
          <input type="text" id="category" list="categories" value="${esc(m.category)}">
          <datalist id="categories">${categories.map((c) => `<option value="${c}">`).join("")}</datalist></div>
        <div><label for="topic">Temat</label><input type="text" id="topic" value="${esc(m.topic)}"></div>
        <div><label for="video_url">Link do wideo (YouTube lub plik)</label><input type="url" id="video_url" value="${esc(m.video_url)}"></div>
      </div>
      <label for="angle">Kąt redakcyjny (opcjonalnie)</label>
      <input type="text" id="angle" value="${esc(m.angle)}" placeholder="np. koszty dla gospodarstw domowych">
      <label for="style">Profil stylu</label>
      <select id="style"><option value="">— bez profilu —</option>
        ${profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === m.style_profile_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}
      </select>
      <label for="transcript">Transkrypcja</label>
      <p class="small muted">Jedna wypowiedź na linię: <code>[00:01:23] Imię Nazwisko: treść</code> lub <code>[01:23] treść</code>. Linie bez znacznika czasu są doklejane do poprzedniej wypowiedzi.</p>
      <textarea id="transcript" class="mono" rows="16" required>${esc(m.transcript_raw)}</textarea>
      <label class="check"><input type="checkbox" id="has-speakers" ${m.transcript_has_speakers === false ? "" : "checked"}>
        <span>Linie zawierają mówców (<code>Mówca: treść</code>). Odznacz dla transkrypcji automatycznej bez mówców.</span></label>
      <label class="check"><input type="checkbox" id="consent" ${m.external_processing_allowed ? "checked" : ""}>
        <span>Materiał jest publiczny, syntetyczny lub polityka redakcji pozwala wysłać go do zewnętrznego API modelu językowego.
        <span class="muted small">Bez tej zgody analiza AI nie zostanie uruchomiona.</span></span></label>
      ${id ? `<p class="small muted">Zmiana transkrypcji usuwa dotychczasową analizę i draft.</p>` : ""}
      <div class="row" style="margin-top:1rem">
        <button class="primary" type="submit">${id ? "Zapisz" : "Utwórz materiał"}</button>
        <a class="button" href="${id ? `#/m/${id}` : "#/"}">Anuluj</a>
        <span class="spacer"></span>
        ${id ? `<button type="button" id="delete">Usuń materiał</button>` : ""}
      </div>
    </form>`;

  const form = view.querySelector("#material-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = {
      title: form.querySelector("#title").value.trim(),
      category: form.querySelector("#category").value.trim(),
      topic: form.querySelector("#topic").value.trim(),
      angle: form.querySelector("#angle").value.trim(),
      video_url: form.querySelector("#video_url").value.trim(),
      transcript_raw: form.querySelector("#transcript").value,
      transcript_has_speakers: form.querySelector("#has-speakers").checked,
      external_processing_allowed: form.querySelector("#consent").checked,
      style_profile_id: form.querySelector("#style").value || null,
    };
    busy(event.submitter, "Zapisywanie…", async () => {
      const saved = await api(id ? `/api/materials/${id}` : "/api/materials", { method: id ? "PUT" : "POST", body });
      location.hash = `#/m/${saved.id}`;
    });
  });
  view.querySelector("#delete")?.addEventListener("click", (e) => {
    if (!confirm("Usunąć materiał wraz z draftem?")) return;
    busy(e.target, "Usuwanie…", async () => {
      await api(`/api/materials/${id}`, { method: "DELETE" });
      location.hash = "#/";
    });
  });
}

// --- przestrzeń robocza ------------------------------------------------------

async function renderWorkspace(id) {
  clearTimeout(renderWorkspace.timer);
  const [material, profiles] = await Promise.all([api(`/api/materials/${id}`), api("/api/style-profiles")]);
  state.material = material;
  state.profiles = profiles;
  if (material.status === "PROCESSING" || (material.status === "FAILED" && !material.transcript)) {
    drawProcessing(material);
    if (material.status === "PROCESSING") {
      renderWorkspace.timer = setTimeout(() => { if (location.hash === `#/m/${id}`) renderWorkspace(id); }, 2000);
    }
    return;
  }
  if (material.job_stage === "DONE" && renderWorkspace.lastStage === "PROCESSING") state.tab = "draft";
  renderWorkspace.lastStage = material.status;
  if (!material.draft && state.tab === "draft" && !material.evidence) state.tab = "evidence";
  drawWorkspace();
}

function drawWorkspace() {
  const m = state.material;
  const [label, cls] = STATUS[m.status];
  view.innerHTML = `
    <div class="row">
      <h1 style="margin:0">${esc(m.title)}</h1>
      <span class="badge ${cls}">${label}</span>
      <span class="spacer"></span>
      <button id="auto-run" title="Analiza, wybór trybu, draft w stylu profilu i weryfikacja – bez dalszych kliknięć">⚡ Automat</button>
      <a class="button" href="#/m/${m.id}/edit">Ustawienia materiału</a>
    </div>
    ${m.status === "FAILED" ? `<div class="verify err"><strong>Automat zakończył się błędem:</strong> ${esc(m.job_error || "nieznany błąd")}</div>` : ""}
    <p class="muted small">${[m.category, m.topic, m.angle && "kąt: " + m.angle].filter(Boolean).map(esc).join(" · ")}</p>
    <div class="workspace">
      <aside class="card sticky" id="transcript-panel">${transcriptPanel(m)}</aside>
      <section>
        <div class="tabs">
          ${[["evidence", "Materiał dowodowy"], ["draft", "Draft"], ["export", "Eksport"]]
            .map(([key, name]) => `<button data-tab="${key}" class="${state.tab === key ? "active" : ""}">${name}</button>`).join("")}
        </div>
        <div id="tab-body"></div>
      </section>
    </div>`;

  view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.tab; drawWorkspace(); }));
  view.querySelector("#auto-run").addEventListener("click", (e) => {
    if (m.draft && !confirm("Automat wygeneruje nowy draft i zastąpi bieżący. Kontynuować?")) return;
    busy(e.target, "Uruchamiam…", async () => {
      await api(`/api/materials/${m.id}/auto`, { method: "POST", body: {} });
      renderWorkspace.lastStage = "PROCESSING";
      renderWorkspace(m.id);
    });
  });
  bindTranscript();
  const body = view.querySelector("#tab-body");
  if (state.tab === "evidence") drawEvidence(body);
  else if (state.tab === "draft") drawDraft(body);
  else drawExport(body);
}

function transcriptPanel(m) {
  const yt = youtubeId(m.video_url);
  let player = "";
  if (yt) player = `<iframe id="player" class="video" src="https://www.youtube-nocookie.com/embed/${esc(yt)}" allow="autoplay; encrypted-media" allowfullscreen title="Wideo"></iframe>`;
  else if (isDirectVideo(m.video_url)) player = `<video id="player" class="video" controls preload="metadata" src="${esc(m.video_url)}"></video>`;
  else if (m.media_file) player = `<video id="player" class="video" controls preload="metadata" src="/media/${esc(m.id)}"></video>`;

  const segments = m.transcript.segments.map((s) => `
    <div class="segment" id="seg-${s.start_seconds}">
      <button class="link tc" data-seek="${s.start_seconds}" title="Przejdź do momentu w nagraniu">${tc(s.start_seconds)}</button>
      <span class="who">${esc(s.speaker || "")}</span>
      <span class="text">${esc(s.text)}</span>
    </div>`).join("");

  return `${player}
    <div class="row"><h2 style="margin:0">Transkrypcja</h2><span class="spacer"></span>
      <input type="text" id="seg-filter" placeholder="Szukaj…" style="max-width:12rem"></div>
    <p class="small muted">Tekst transkrypcji nie zawiera tego, co widać w kadrze. Sprawdź zgodność z nagraniem.</p>
    <div id="segments">${segments}</div>`;
}

function seek(seconds) {
  const m = state.material;
  const player = document.getElementById("player");
  const yt = youtubeId(m.video_url);
  if (player && yt) player.src = `https://www.youtube-nocookie.com/embed/${yt}?start=${seconds}&autoplay=1`;
  else if (player && player.tagName === "VIDEO") { player.currentTime = seconds; player.play().catch(() => {}); }
  else if (m.video_url) window.open(videoLink(m.video_url, seconds), "_blank", "noopener");
  highlight(seconds);
}

function highlight(seconds) {
  document.querySelectorAll(".segment.hl").forEach((el) => el.classList.remove("hl"));
  const el = document.getElementById(`seg-${seconds}`);
  if (el) { el.classList.add("hl"); el.scrollIntoView({ block: "center", behavior: "smooth" }); }
}

function bindTranscript() {
  view.querySelectorAll("#transcript-panel [data-seek]").forEach((b) => b.addEventListener("click", () => seek(Number(b.dataset.seek))));
  view.querySelector("#seg-filter")?.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    view.querySelectorAll("#segments .segment").forEach((el) => (el.style.display = el.textContent.toLowerCase().includes(q) ? "" : "none"));
  });
}

// Odnośnik do segmentu transkrypcji w panelu po prawej.
function tcLink(seconds) {
  return `<button class="link tc small" data-goto="${seconds}">${tc(seconds)}</button>`;
}

function bindGoto(root) {
  root.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => highlight(Number(b.dataset.goto))));
}

// --- zakładka: materiał dowodowy ---------------------------------------------

function drawEvidence(root) {
  const m = state.material;
  const e = m.evidence;
  if (!e) {
    root.innerHTML = `<div class="card empty">
      <p>Najpierw porządkujemy materiał dowodowy: wątki, cytaty z timecode i tezy do weryfikacji.<br>Dopiero na tej podstawie powstaje draft.</p>
      <button class="primary" id="analyze">Analizuj transkrypcję</button></div>`;
    root.querySelector("#analyze").addEventListener("click", (ev) => analyze(ev.target));
    return;
  }

  root.innerHTML = `
    <div class="card">
      <div class="row"><h2 style="margin:0">Podsumowanie</h2><span class="spacer"></span>
        <button id="analyze">Analizuj ponownie</button></div>
      <p>${esc(e.summary)}</p>
      <p><span class="badge ${e.suggested_mode === "FULL" ? "ok" : "warn"}">Sugerowany tryb: ${e.suggested_mode}</span>
        <span class="small muted">${esc(e.mode_rationale)}</span></p>
    </div>
    <div class="card"><h2>Wątki</h2><ul class="list">
      ${e.threads.map((t) => `<li><strong>${esc(t.name)}</strong> — ${esc(t.summary)}<div>${t.timecodes.map(tcLink).join(" ")}</div></li>`).join("") || "<li class='muted'>Brak</li>"}
    </ul></div>
    <div class="card"><h2>Cytaty (zweryfikowane w transkrypcji)</h2><ul class="list">
      ${e.quotes.map((q) => `<li>${tcLink(q.timecode_seconds)} <strong>${esc(q.speaker || "mówca nieznany")}</strong><blockquote>„${esc(q.text)}”</blockquote></li>`).join("") || "<li class='muted'>Brak</li>"}
    </ul></div>
    <div class="card"><h2>Tezy i fakty</h2><ul class="list">
      ${e.claims.map((c) => `<li>${c.requires_verification ? '<span class="badge err">do weryfikacji</span> ' : ""}${esc(c.statement)}
        ${c.note ? `<div class="small muted">${esc(c.note)}</div>` : ""}<div>${c.evidence_timecodes.map(tcLink).join(" ")}</div></li>`).join("") || "<li class='muted'>Brak</li>"}
    </ul></div>
    <div class="card"><h2>Luki i niepewności</h2><ul class="list">
      ${e.gaps.map((g) => `<li>${esc(g)}</li>`).join("") || "<li class='muted'>Brak</li>"}
    </ul>
    <div class="row" style="margin-top:.8rem"><span class="spacer"></span>
      <button class="primary" id="to-draft">Przejdź do draftu →</button></div></div>`;

  root.querySelector("#analyze").addEventListener("click", (ev) => analyze(ev.target));
  root.querySelector("#to-draft").addEventListener("click", () => { state.tab = "draft"; drawWorkspace(); });
  bindGoto(root);
}

function analyze(button) {
  return busy(button, "Analizuję…", async () => {
    state.material = await api(`/api/materials/${state.material.id}/analyze`, { method: "POST" });
    state.tab = "evidence";
    drawWorkspace();
    toast("Materiał dowodowy gotowy");
  });
}

// --- zakładka: draft ---------------------------------------------------------

function generatorCard(m) {
  const suggested = m.evidence?.suggested_mode;
  const current = m.mode || suggested || "SHORT";
  const option = (mode, title, desc) => `
    <label><input type="radio" name="mode" value="${mode}" ${current === mode ? "checked" : ""}>
      <strong>${title}</strong>${suggested === mode ? ' <span class="badge ok">sugerowany</span>' : ""}
      <div class="small muted">${desc}</div></label>`;
  return `
    <div class="card">
      <h2>${m.draft ? "Wygeneruj ponownie" : "Wygeneruj draft"}</h2>
      <div class="mode-choice">
        ${option("SHORT", "SHORT", "Tekst pod wideo: tytuły i jeden akapit (40–90 słów).")}
        ${option("FULL", "FULL", "Pełny artykuł: lead, śródtytuły, cytaty z myślnikiem (300–850 słów).")}
      </div>
      <div class="grid2">
        <div><label for="target">Docelowa długość (słowa, opcjonalnie)</label><input type="number" id="target" min="30" max="3000" placeholder="wynika z materiału"></div>
        <div><label for="profile">Profil stylu</label><select id="profile"><option value="">— bez profilu —</option>
          ${state.profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === m.style_profile_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>
      </div>
      <label for="instructions">Wskazówki dla tej wersji (opcjonalnie)</label>
      <input type="text" id="instructions" placeholder="np. zacznij od wypowiedzi przeciwników kaucji">
      <div class="row" style="margin-top:.8rem">
        <button class="primary" id="generate">${m.draft ? "Wygeneruj nową wersję" : "Generuj draft"}</button>
        ${m.draft ? '<span class="small muted">Nowa wersja zastąpi bieżący draft wraz z ręcznymi zmianami.</span>' : ""}
      </div>
    </div>`;
}

function formatBox(report) {
  const warnings = report?.format_warnings || [];
  if (!warnings.length) return "";
  return `<div class="verify warn"><strong>Format WP Finanse:</strong> odstępstwa do rozważenia (nie blokują eksportu).<ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
}

function verificationBox(report) {
  if (!report) return "";
  if (report.ok) return `<div class="verify ok"><strong>✓ Weryfikacja przeszła.</strong> Wszystkie cytaty występują w transkrypcji, tezy mają wskazane źródła.</div>${formatBox(report)}`;
  const items = [];
  report.quotes.filter((q) => q.status !== "VERIFIED").forEach((q) => items.push(`Cytat „${esc(q.quote.text)}”: ${esc(q.message)}`));
  report.claims.forEach((c) => items.push(`Teza „${esc(c.statement)}”: ${esc(c.problems.join("; "))}`));
  report.inline_quotes.filter((q) => !q.verified).forEach((q) => items.push(`${esc(q.location)}: „${esc(q.text)}” nie występuje w transkrypcji`));
  return `<div class="verify err"><strong>✗ Draft nie przeszedł weryfikacji.</strong> Popraw przed zatwierdzeniem i eksportem.<ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul></div>${formatBox(report)}`;
}

function drawDraft(root) {
  const m = state.material;
  const d = m.draft;
  if (!d) {
    root.innerHTML = generatorCard(m);
    bindGenerator(root);
    return;
  }

  const claimFlag = (c) => (c.requires_verification ? '<span class="badge err">do weryfikacji</span> ' : "");
  root.innerHTML = `
    ${verificationBox(m.report)}
    <form class="card paper-form" id="draft-form">
      <div class="row"><h2 style="margin:0">Draft</h2><span class="badge">${d.mode}</span><span class="spacer"></span>
        <span class="small muted" id="wordcount"></span></div>
      <label>Propozycje tytułu (pierwsza trafi do eksportu HTML, chyba że wybierzesz inną)</label>
      <div id="titles">${d.title_options.map((t) => `<input type="text" class="title-input paper" value="${esc(t)}" style="margin-bottom:.4rem">`).join("")}</div>
      <div class="row small">
        ${d.title_options.length < 3 ? '<button type="button" class="link" id="add-title">+ dodaj tytuł</button>' : ""}
        ${d.title_options.length > 2 ? '<button type="button" class="link" id="remove-title">− usuń ostatni tytuł</button>' : ""}
      </div>
      <label for="lead">${d.mode === "SHORT" ? "Tekst pod wideo (jeden akapit)" : "Lead"}</label>
      <textarea id="lead" rows="3" class="paper">${esc(d.lead)}</textarea>
      <div id="sections">${d.sections.map(sectionEditor).join("")}</div>
      <button type="button" class="link" id="add-section">+ dodaj sekcję</button>

      <h3 style="margin-top:1.2rem">Cytaty</h3>
      <p class="small muted">Cytat w cudzysłowie „…” lub akapit zaczynający się od myślnika („- treść - mówi X”) musi występować dosłownie w transkrypcji – sprawdzamy to przy każdym zapisie.</p>
      <ul class="list" id="quotes">${d.quotes.map((q, i) => `<li data-i="${i}">${tcLink(q.timecode_seconds)} <strong>${esc(q.speaker || "mówca nieznany")}</strong>: „${esc(q.text)}”
        <button type="button" class="icon" data-remove-quote="${i}" title="Usuń z listy">✕</button></li>`).join("") || "<li class='muted'>Brak</li>"}</ul>

      <h3 style="margin-top:1rem">Tezy i fakty</h3>
      <ul class="list" id="claims">${d.claims.map((c, i) => `<li>${claimFlag(c)}${esc(c.statement)} ${c.evidence_timecodes.map(tcLink).join(" ")}
        ${c.note ? `<div class="small muted">${esc(c.note)}</div>` : ""}
        <button type="button" class="icon" data-remove-claim="${i}" title="Usuń">✕</button></li>`).join("") || "<li class='muted'>Brak</li>"}</ul>

      <label for="notes">Uwagi dla redaktora (jedna na linię)</label>
      <textarea id="notes" rows="4">${esc(d.editor_notes.join("\n"))}</textarea>

      <div class="row" style="margin-top:1rem">
        <button class="primary" type="submit">Zapisz i sprawdź</button>
        <button type="button" id="approve" ${m.report?.ok ? "" : "disabled"} title="Wymaga pozytywnej weryfikacji">${m.status === "APPROVED" ? "Zatwierdzony ✓" : "Zatwierdź draft"}</button>
        <span class="spacer"></span>
        <button type="button" id="to-export">Eksport →</button>
      </div>
    </form>
    ${generatorCard(m)}`;

  const form = root.querySelector("#draft-form");
  const updateCount = () => {
    const text = [...form.querySelectorAll(".paragraphs")].map((t) => t.value).join(" ") + " " + form.querySelector("#lead").value;
    form.querySelector("#wordcount").textContent = `${text.split(/\s+/).filter(Boolean).length} słów (lead + treść)`;
  };
  updateCount();
  form.addEventListener("input", updateCount);

  form.querySelector("#add-title")?.addEventListener("click", () => mutateDraft((x) => x.title_options.push("")));
  form.querySelector("#remove-title")?.addEventListener("click", () => mutateDraft((x) => x.title_options.pop()));
  form.querySelector("#add-section").addEventListener("click", () => mutateDraft((x) => x.sections.push({ heading: x.mode === "FULL" ? "Nowy śródtytuł" : null, paragraphs: [""] })));
  form.querySelectorAll("[data-remove-section]").forEach((b) => b.addEventListener("click", () => mutateDraft((x) => x.sections.splice(Number(b.dataset.removeSection), 1))));
  form.querySelectorAll("[data-remove-quote]").forEach((b) => b.addEventListener("click", () => mutateDraft((x) => x.quotes.splice(Number(b.dataset.removeQuote), 1))));
  form.querySelectorAll("[data-remove-claim]").forEach((b) => b.addEventListener("click", () => mutateDraft((x) => x.claims.splice(Number(b.dataset.removeClaim), 1))));

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(event.submitter, "Sprawdzam…", async () => {
      state.material = await api(`/api/materials/${m.id}/draft`, { method: "PUT", body: readDraft(form) });
      drawWorkspace();
      toast(state.material.report.ok ? "Zapisano – weryfikacja OK" : "Zapisano – są problemy do poprawy", !state.material.report.ok);
    });
  });
  form.querySelector("#approve").addEventListener("click", (e) => busy(e.target, "…", async () => {
    state.material = await api(`/api/materials/${m.id}/approve`, { method: "POST" });
    drawWorkspace();
    toast("Draft zatwierdzony");
  }));
  form.querySelector("#to-export").addEventListener("click", () => { state.tab = "export"; drawWorkspace(); });
  bindGoto(root);
  bindGenerator(root);
}

function sectionEditor(section, index) {
  return `<div class="section-editor" data-section="${index}">
    <div class="row"><input type="text" class="heading paper" placeholder="Śródtytuł (puste = bez śródtytułu)" value="${esc(section.heading || "")}" style="flex:1">
      <button type="button" class="icon" data-remove-section="${index}" title="Usuń sekcję">✕</button></div>
    <textarea class="paragraphs paper" rows="${Math.max(4, section.paragraphs.length * 3)}" placeholder="Akapity oddzielone pustą linią">${esc(section.paragraphs.join("\n\n"))}</textarea>
  </div>`;
}

// Odczyt formularza do modelu ArticleDraft; quotes/claims pochodzą z bieżącego stanu.
function readDraft(form) {
  const d = state.material.draft;
  return {
    mode: d.mode,
    title_options: [...form.querySelectorAll(".title-input")].map((i) => i.value.trim()).filter(Boolean),
    lead: form.querySelector("#lead").value.trim(),
    sections: [...form.querySelectorAll(".section-editor")].map((el) => ({
      heading: el.querySelector(".heading").value.trim() || null,
      paragraphs: el.querySelector(".paragraphs").value.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean),
    })),
    quotes: d.quotes,
    claims: d.claims,
    editor_notes: form.querySelector("#notes").value.split("\n").map((n) => n.trim()).filter(Boolean),
  };
}

// Zmiana struktury (dodanie/usunięcie elementu) bez utraty niezapisanych edycji tekstu.
function mutateDraft(change) {
  const form = document.getElementById("draft-form");
  const draft = readDraft(form);
  // readDraft pomija puste tytuły; przy dodawaniu chcemy zachować nowe puste pole.
  draft.title_options = [...form.querySelectorAll(".title-input")].map((i) => i.value.trim());
  change(draft);
  state.material = { ...state.material, draft };
  drawWorkspace();
}

function bindGenerator(root) {
  root.querySelector("#generate")?.addEventListener("click", (event) => {
    const m = state.material;
    if (m.draft && !confirm("Wygenerować nową wersję? Bieżący draft zostanie zastąpiony.")) return;
    const body = {
      mode: root.querySelector("input[name=mode]:checked").value,
      target_words: Number(root.querySelector("#target").value) || null,
      extra_instructions: root.querySelector("#instructions").value.trim(),
      style_profile_id: root.querySelector("#profile").value || null,
    };
    busy(event.target, "Generuję draft…", async () => {
      const result = await api(`/api/materials/${m.id}/draft`, { method: "POST", body });
      state.material = result.material;
      state.tab = "draft";
      drawWorkspace();
      toast(result.repaired ? "Draft gotowy (po automatycznej poprawce cytatów)" : "Draft gotowy");
    });
  });
}

// --- zakładka: eksport -------------------------------------------------------

async function drawExport(root) {
  const m = state.material;
  if (!m.draft) {
    root.innerHTML = `<div class="card empty">Najpierw wygeneruj draft.</div>`;
    return;
  }
  root.innerHTML = `
    ${verificationBox(m.report)}
    <div class="card">
      <h2>Eksport do CMS (ręczny)</h2>
      <p class="small muted">Eksport jest możliwy tylko dla draftu, który przeszedł weryfikację. Materiał kontrolny (cytaty z timecode, tezy, uwagi) nie jest przeznaczony do publikacji.</p>
      <div class="grid2">
        <div><label for="fmt">Format</label><select id="fmt"><option value="md">Markdown</option><option value="html">HTML</option></select></div>
        <div><label for="ttl">Tytuł (HTML)</label><select id="ttl">${m.draft.title_options.map((t, i) => `<option value="${i}">${esc(t)}</option>`).join("")}</select></div>
      </div>
      <label class="check"><input type="checkbox" id="ctl" checked> Dołącz materiał kontrolny</label>
      <div class="row" style="margin:.8rem 0"><button class="primary" id="copy">Kopiuj</button><button id="download">Pobierz plik</button></div>
      <pre class="export" id="out"></pre>
    </div>`;

  const out = root.querySelector("#out");
  let text = "";
  const refresh = async () => {
    const fmt = root.querySelector("#fmt").value;
    const params = new URLSearchParams({ format: fmt, control: root.querySelector("#ctl").checked, title: root.querySelector("#ttl").value });
    try {
      text = await api(`/api/materials/${m.id}/export?${params}`);
      out.textContent = text;
    } catch (error) {
      text = "";
      out.textContent = "Eksport zablokowany: " + error.message;
    }
  };
  root.querySelectorAll("select, input").forEach((el) => el.addEventListener("change", refresh));
  root.querySelector("#copy").addEventListener("click", async () => {
    if (!text) return toast("Brak treści do skopiowania", true);
    try { await navigator.clipboard.writeText(text); toast("Skopiowano"); } catch (_) { toast("Przeglądarka zablokowała schowek – zaznacz tekst ręcznie", true); }
  });
  root.querySelector("#download").addEventListener("click", () => {
    if (!text) return toast("Brak treści do pobrania", true);
    const fmt = root.querySelector("#fmt").value;
    const blob = new Blob([text], { type: fmt === "md" ? "text/markdown" : "text/html" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `${m.title.replace(/[^\p{L}\p{N}]+/gu, "-").toLowerCase()}.${fmt}` });
    a.click();
    URL.revokeObjectURL(a.href);
  });
  await refresh();
}

// --- profile stylu -----------------------------------------------------------

const PROFILE_FIELDS = [
  ["title_style", "Tytuły"], ["lead_style", "Lead"], ["paragraph_style", "Akapity"], ["subheading_style", "Śródtytuły"],
  ["quote_style", "Cytowanie"], ["tone", "Ton"], ["context_style", "Kontekst"], ["multi_speaker_style", "Wielu rozmówców"],
  ["short_mode_notes", "Tryb SHORT"], ["full_mode_notes", "Tryb FULL"],
];

async function renderStyles() {
  const profiles = await api("/api/style-profiles");
  view.innerHTML = `
    <h1>Profile stylu</h1>
    <p class="muted">Profil to jawny opis formatu (długości, tytuły, cytowanie, ton) wyprowadzony z kilku publicznych artykułów.
      Służy spójności formatu – nie kopiowaniu treści ani podszywaniu się pod autora. Teksty artykułów nie są zapisywane.</p>
    ${profiles.map(profileCard).join("")}
    <form class="card" id="new-profile">
      <h2>Nowy profil</h2>
      <div class="grid2">
        <div><label for="pname">Nazwa</label><input type="text" id="pname" required placeholder="np. Finanse – wideo"></div>
        <div><label for="pdesc">Opis</label><input type="text" id="pdesc" placeholder="skąd pochodzą przykłady"></div>
      </div>
      <label for="particles">Artykuły referencyjne (5–10 najlepiej; oddziel linią z <code>---</code>)</label>
      <p class="small muted">Format każdego: tytuł w pierwszej linii, pusta linia, lead, pusta linia, dalsze akapity. Tylko materiały publiczne lub legalnie udostępnione.</p>
      <textarea id="particles" rows="14" class="mono" required></textarea>
      <div class="row" style="margin-top:.8rem"><button class="primary" type="submit">Utwórz profil</button></div>
    </form>`;

  view.querySelector("#new-profile").addEventListener("submit", (event) => {
    event.preventDefault();
    const articles = view.querySelector("#particles").value.split(/^\s*---\s*$/m).map((a) => a.trim()).filter(Boolean);
    const body = { name: view.querySelector("#pname").value.trim(), description: view.querySelector("#pdesc").value.trim(), articles };
    busy(event.submitter, "Analizuję artykuły…", async () => {
      await api("/api/style-profiles", { method: "POST", body });
      toast("Profil utworzony");
      renderStyles();
    });
  });

  view.querySelectorAll("form[data-profile]").forEach((form) => {
    const record = profiles.find((p) => p.id === form.dataset.profile);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const profile = { ...record.profile };
      PROFILE_FIELDS.forEach(([key]) => (profile[key] = form.querySelector(`[name=${key}]`).value));
      profile.typical_title_words = Number(form.querySelector("[name=typical_title_words]").value) || 1;
      profile.typical_lead_words = Number(form.querySelector("[name=typical_lead_words]").value) || 1;
      busy(event.submitter, "Zapisywanie…", async () => {
        await api(`/api/style-profiles/${record.id}`, { method: "PUT", body: { name: form.querySelector("[name=name]").value, description: record.description, profile } });
        toast("Zapisano profil");
      });
    });
    form.querySelector("[data-delete]").addEventListener("click", (e) => {
      if (!confirm(`Usunąć profil „${record.name}”?`)) return;
      busy(e.target, "…", async () => { await api(`/api/style-profiles/${record.id}`, { method: "DELETE" }); renderStyles(); });
    });
  });
}

function profileCard(record) {
  const p = record.profile;
  return `<form class="card" data-profile="${esc(record.id)}">
    <details>
      <summary class="row" style="cursor:pointer"><strong>${esc(record.name)}</strong>
        <span class="small muted">${esc(record.description)} · ${record.source_count} artykułów · tytuł ~${p.typical_title_words} słów · lead ~${p.typical_lead_words} słów</span></summary>
      <label>Nazwa</label><input type="text" name="name" value="${esc(record.name)}">
      <div class="grid2">
        ${PROFILE_FIELDS.map(([key, label]) => `<div><label>${label}</label><textarea name="${key}" rows="2">${esc(p[key])}</textarea></div>`).join("")}
        <div><label>Typowa liczba słów w tytule</label><input type="number" name="typical_title_words" min="1" value="${p.typical_title_words}"></div>
        <div><label>Typowa liczba słów w leadzie</label><input type="number" name="typical_lead_words" min="1" value="${p.typical_lead_words}"></div>
      </div>
      <div class="row" style="margin-top:.8rem"><button class="primary" type="submit">Zapisz</button><span class="spacer"></span>
        <button type="button" data-delete>Usuń</button></div>
    </details>
  </form>`;
}

// --- start -------------------------------------------------------------------

loadConfig().catch((error) => toast(error.message, true)).finally(route);
