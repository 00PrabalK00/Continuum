/* Continuum, four views.
 *
 * The old page had eight nav items, an inspector and an activity drawer, all
 * competing for the same attention while the thing people actually open it for,
 * where does this project stand, was buried. Now answers that in the biggest
 * type on the page. History and Notes are the two ways to interrogate it.
 * Everything else lives behind Advanced, demoted rather than deleted.
 */

const VIEWS = ["now", "history", "notes", "advanced"];
const state = { view: "now", data: {}, diff: null, blame: null };

/* Escaped by default. Every string on this page is recorded by an agent or
   typed by a person, so none of it is trusted markup. */
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => ({ error: "The server sent something that is not JSON." }));
  if (!response.ok || body.error) throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}

function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2600);
}

function when(value) {
  if (!value) return "";
  return String(value).slice(0, 16).replace("T", " ");
}

function ago(days) {
  if (days === null || days === undefined) return "";
  if (days === 0) return "today";
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

/* Empty is a state with something to say, not a blank panel. */
function nothing(text) {
  return `<p class="empty-state">${text}</p>`;
}

function card(inner) {
  return `<section class="card">${inner}</section>`;
}

function list(items, render) {
  if (!items || !items.length) return "";
  return `<ul class="plain">${items.map(render).join("")}</ul>`;
}

/* Now ---------------------------------------------------------------- */

function renderNow(now) {
  const headline = now.task
    ? `<h1 class="headline">${esc(now.task)}</h1>`
    : `<h1 class="headline empty">Nothing recorded on this branch yet.</h1>`;

  const next = now.next_step
    ? `<div><p class="label">Next</p><p class="next">${esc(now.next_step)}</p></div>`
    : "";

  const meta = [];
  if (now.age_days !== null && now.age_days !== undefined) meta.push(`Recorded ${ago(now.age_days)}`);
  if (now.branch) meta.push(`Branch <span class="mono">${esc(now.branch)}</span>`);
  if (now.recorded_against) meta.push(`Against <span class="mono">${esc(now.recorded_against)}</span>`);

  const drift = now.drift ? `<div class="drift">${esc(now.drift)}</div>` : "";

  const claims = [];
  if (now.decisions.length) {
    claims.push(card(`<p class="label">Decisions</p>${list(now.decisions, (t) => `<li>${esc(t)}</li>`)}`));
  }
  if (now.open_questions.length) {
    claims.push(card(
      `<p class="label">Open questions</p>` +
      list(now.open_questions, (t) => `<li>${esc(t)} <span class="pill open">open</span></li>`)
    ));
  }
  if (now.facts.length) {
    claims.push(card(`<p class="label">Observed</p>${list(now.facts, (t) => `<li>${esc(t)}</li>`)}`));
  }

  const empty = !now.task && !claims.length
    ? card(nothing(
        `Run <code>continuum save "what you did | next: what is next"</code> in this project, ` +
        `or let an agent record it as it works. This page reads what is in <code>.continuum/</code>.`))
    : "";

  return `<div class="stack">
    ${card(`<div class="stack">${headline}${next}
      ${meta.length ? `<p class="meta">${meta.join(" &middot; ")}</p>` : ""}</div>`)}
    ${drift}
    ${claims.length ? `<div class="row">${claims.join("")}</div>` : ""}
    ${empty}
  </div>`;
}

/* History ------------------------------------------------------------ */

function renderHistory(checkpoints, branches) {
  if (!checkpoints.length) {
    return card(nothing(`No checkpoints yet. Each <code>continuum save</code> writes one, and so does the
      end of an agent session.`));
  }

  const rows = checkpoints.map((item) => `
    <button class="rowitem" data-diff="${esc(item.ref)}">
      <span class="ref">${esc(item.ref)}</span>
      <span class="what">${esc(item.task)}</span>
      <span class="when">${esc(when(item.created_at))}${item.commit ? ` &middot; <span class="mono">${esc(item.commit)}</span>` : ""}</span>
    </button>`).join("");

  const others = branches.filter((b) => !b.current);
  const branchNote = others.length
    ? card(`<p class="label">Other branches</p>${list(others, (b) =>
        `<li><span class="mono">${esc(b.name)}</span> &middot; ${esc(b.task || "nothing recorded")}
         <span class="faint small">(${esc(b.checkpoints)})</span></li>`)}`)
    : "";

  const diff = state.diff
    ? card(`<p class="label">${esc(state.diff.older)} to ${esc(state.diff.newer)}</p>
        <pre class="diff">${esc(state.diff.diff)}</pre>`)
    : "";

  return `<div class="stack">
    ${card(`<p class="label">Find where a claim came from</p>
      <div class="row" style="align-items:center">
        <input class="field" id="blame-q" placeholder="BillingGateway" value="${esc(state.blame ? state.blame.query : "")}">
        <button class="btn" id="blame-go" style="flex:0 0 auto">Search</button>
      </div>
      ${state.blame ? `<p class="muted small" style="margin:14px 0 0">${esc(state.blame.summary)}</p>` : ""}`)}
    ${card(`<p class="label">Checkpoints, newest first. Select one to compare it with the newest.</p>
      <div class="rows">${rows}</div>`)}
    ${diff}
    ${branchNote}
  </div>`;
}

/* Notes -------------------------------------------------------------- */

function renderNotes(notes) {
  if (!notes.length) {
    return card(nothing(`Nothing recorded yet. <code>continuum note decision "chose PostgreSQL"</code>
      records one; hypothesis and fact are the other two kinds. An open hypothesis is shown to the
      next agent as unsettled rather than as established.`));
  }

  const rows = notes.map((item) => {
    const mark = item.state === "open" ? "open" : "";
    const shown = item.state ? ` <span class="pill ${esc(mark)}">${esc(item.state)}</span>` : "";
    return `<li><span class="pill ${item.type === "decision" ? "decision" : ""}">${esc(item.type)}</span>
      ${esc(item.text)}${shown}
      <span class="faint small">&middot; ${esc(when(item.created_at))}</span></li>`;
  }).join("");

  return card(`<p class="label">Decisions, hypotheses and facts</p><ul class="plain">${rows}</ul>`);
}

/* Advanced ----------------------------------------------------------- */

function renderAdvanced(data) {
  const sections = [];

  sections.push(card(`<p class="label">Providers</p>${
    data.providers.length
      ? list(data.providers, (p) => `<li><span class="mono">${esc(p.name)}</span>
          <span class="faint small">${esc(p.kind || "")} ${esc(p.type || "")}</span></li>`)
      : nothing("No providers configured. <code>continuum providers</code> lists what is available.")
  }`));

  sections.push(card(`<p class="label">Teams</p>${
    data.teams.length
      ? list(data.teams, (t) => `<li><span class="mono">${esc(t.name)}</span>
          <span class="faint small">${esc((t.roles || []).length)} roles</span></li>`)
      : nothing("No teams configured. <code>continuum team init default_dev_team</code> creates one.")
  }`));

  sections.push(card(`<p class="label">Tasks</p>${
    data.tasks.length
      ? list(data.tasks.slice(0, 8), (t) => `<li><span class="mono">${esc(t.task_id)}</span>
          ${esc(t.title || "")} <span class="faint small">${esc(t.status || "")}</span></li>`)
      : nothing("No controlled tasks. <code>continuum task create</code> opens one.")
  }`));

  sections.push(card(`<p class="label">Recent events</p>${
    data.events.length
      ? list(data.events.slice(0, 12), (e) => `<li><span class="mono small">${esc(e.kind)}</span>
          <span class="faint small">${esc(when(e.created_at))}</span></li>`)
      : nothing("Nothing recorded yet.")
  }`));

  return `<div class="stack">
    ${card(nothing(`These are Continuum's orchestration features. You do not need any of them for the
      daily loop, which is <code>continuum go</code> and the Now tab.`))}
    <div class="row">${sections.join("")}</div>
  </div>`;
}

/* Wiring ------------------------------------------------------------- */

let generation = 0;

async function load(view) {
  const node = document.getElementById("view");
  const mine = ++generation;
  /* Switching tabs mid-request left the slower response to overwrite the tab
     you had already moved to, while the highlight and the URL said otherwise. */
  const stale = () => mine !== generation;
  node.innerHTML = card(`<p class="muted">Loading…</p>`);
  try {
    if (view === "now") {
      const now = await api("/api/now");
      if (stale()) return;
      document.getElementById("project").textContent = now.project || "";
      node.innerHTML = renderNow(now);
    } else if (view === "history") {
      const [checkpoints, branches] = await Promise.all([api("/api/checkpoints"), api("/api/branches")]);
      if (stale()) return;
      node.innerHTML = renderHistory(checkpoints, branches);
      /* Only when the controls exist. The empty state renders no search box, and
         wiring it unconditionally threw, so a project with no checkpoints yet
         showed "Could not load this view" instead of how to make one. */
      if (checkpoints.length) wireHistory(checkpoints);
    } else if (view === "notes") {
      const notes = await api("/api/notes");
      if (stale()) return;
      node.innerHTML = renderNotes(notes);
    } else {
      const [providers, teams, tasks, events] = await Promise.all([
        api("/api/providers"), api("/api/teams"), api("/api/tasks"), api("/api/events"),
      ]);
      if (stale()) return;
      node.innerHTML = renderAdvanced({ providers, teams, tasks, events });
    }
  } catch (error) {
    if (stale()) return;
    node.innerHTML = card(nothing(`Could not load this view. ${esc(error.message)}`));
  }
}

function wireHistory(checkpoints) {
  const newest = checkpoints.length ? checkpoints[0].ref : "HEAD";
  document.querySelectorAll("[data-diff]").forEach((button) => {
    button.addEventListener("click", async () => {
      const ref = button.dataset.diff;
      if (ref === newest) { toast("That is the newest checkpoint."); return; }
      try {
        state.diff = await api(`/api/diff?older=${encodeURIComponent(ref)}&newer=${encodeURIComponent(newest)}`);
        await load("history");
      } catch (error) { toast(error.message); }
    });
  });

  const run = async () => {
    const query = document.getElementById("blame-q").value.trim();
    if (!query) { toast("Type something to look for."); return; }
    try {
      state.blame = await api(`/api/blame?q=${encodeURIComponent(query)}`);
      await load("history");
    } catch (error) { toast(error.message); }
  };
  document.getElementById("blame-go").addEventListener("click", run);
  document.getElementById("blame-q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") run();
  });
}

function show(view) {
  state.view = VIEWS.includes(view) ? view : "now";
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.classList.toggle("active", tab.dataset.view === state.view));
  location.hash = state.view;
  load(state.view);
}

document.getElementById("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (tab) show(tab.dataset.view);
});

window.addEventListener("hashchange", () => show(location.hash.replace("#", "")));
show(location.hash.replace("#", "") || "now");
