const state = {
  view: "teams", overview: null, project: null, providers: [], teams: [],
  tasks: [], events: [], memory: null, flightRecords: [], timeline: null,
  worktreeBoard: null, contextPackets: [], roi: null
};
const pageMeta = {
  overview: ["Overview", "Monitor the local daemon, memory and planned work."],
  projects: ["Projects", "Inspect the repository and its local memory paths."],
  teams: ["Teams", "Inspect roles and routes configured through Continuum commands."],
  providers: ["Providers", "Inspect CLI agents and model backends configured for this project."],
  memory: ["Memory", "Read compact state and retrieve only relevant stored events."],
  runs: ["Runs", "Inspect controlled tasks, ownership and file claims."],
  handoffs: ["Handoffs", "Read the compact continuation note used across agents."],
  settings: ["Settings", "Review local storage and configured context boundaries."]
};

async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}
const sessionToken = document.querySelector('meta[name="continuum-token"]')?.content || "";
async function post(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Continuum-Token": sessionToken},
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}
function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, value => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[value]));
}
function human(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}
function command(text) {
  return `<p class="field-label">Terminal command</p><pre>${escapeHtml(text)}</pre>`;
}
function field(label, value) {
  return `<div><p class="field-label">${escapeHtml(label)}</p><p class="field-value">${escapeHtml(value ?? "-")}</p></div>`;
}
function inspect(title, body) {
  document.getElementById("inspect-title").textContent = title;
  document.getElementById("inspect-body").innerHTML = body;
}
function viewOnly() {
  document.getElementById("primary-action").innerHTML = `<span class="pill">Explicit controls | CLI remains canonical</span>`;
}
function showError(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 4000);
}
async function loadAll() {
  [
    state.overview, state.project, state.providers, state.teams, state.tasks,
    state.events, state.memory, state.flightRecords, state.timeline,
    state.worktreeBoard, state.contextPackets, state.roi
  ] = await Promise.all([
    api("/api/overview"), api("/api/project"), api("/api/providers"), api("/api/teams"),
    api("/api/tasks"), api("/api/events"), api("/api/memory"), api("/api/flight-records"),
    api("/api/timeline"), api("/api/worktree-board"), api("/api/context-packets"), api("/api/roi")
  ]);
  const daemon = state.overview.daemon;
  document.getElementById("daemon-dot").classList.toggle("running", daemon.running);
  document.getElementById("daemon-label").textContent = daemon.running ? `Daemon running | PID ${daemon.pid}` : "Daemon stopped";
  renderActivity();
  render();
}
function render() {
  const [title, description] = pageMeta[state.view];
  document.getElementById("title").textContent = title;
  document.getElementById("description").textContent = description;
  document.querySelectorAll("#nav button").forEach(button => button.classList.toggle("active", button.dataset.view === state.view));
  viewOnly();
  ({
    overview: renderOverview, projects: renderProjects, teams: renderTeams, providers: renderProviders,
    memory: renderMemory, runs: renderRuns, handoffs: renderHandoffs, settings: renderSettings
  }[state.view])();
}
function taskTable(tasks) {
  if (!tasks.length) return `<div class="empty"><h3>No controlled tasks</h3><p>Create work from the terminal.</p>${command('continuum task create "Fix auth"')}</div>`;
  return `<div class="table"><div class="row header"><span>Task</span><span>Worker</span><span>Status</span><span>Files</span></div>${tasks.map(task => `
    <div class="row selectable task-row" data-task="${task.task_id}"><span>${escapeHtml(task.title)}</span><span>${escapeHtml(task.agent || "Unassigned")}</span><span>${escapeHtml(task.status)}</span><span>${task.locked_files.length} claimed</span></div>`).join("")}</div>`;
}
function bindTasks() {
  document.querySelectorAll(".task-row").forEach(row => row.onclick = () => {
    const task = state.tasks.find(item => item.task_id === row.dataset.task);
    const flight = state.flightRecords.find(item => item.task_id === task.task_id);
    inspect(task.task_id, field("Task", task.title) + field("Status", task.status) + field("Worker", task.agent || "Unassigned") +
      field("Claimed files", task.locked_files.map(lock => lock.path).join(", ") || "None") +
      (flight ? field("Flight status", flight.final_status) + field("Next action", flight.next_action) : "") +
      command(`continuum flight-record ${task.task_id}`));
  });
}
function renderOverview() {
  const overview = state.overview;
  const daemon = overview.daemon.running ? "Running" : "Stopped";
  document.getElementById("content").innerHTML = `
    <div class="grid cards">
      <article class="card selectable" id="daemon-card"><p class="label">Daemon status</p><p class="value">${daemon}</p><p class="detail">${overview.daemon.pid ? `PID ${overview.daemon.pid}` : "No active process"}</p></article>
      <article class="card"><p class="label">Active project</p><p class="value">${escapeHtml(overview.project.name)}</p><p class="detail mono">${escapeHtml(overview.project.branch || "No branch")}</p></article>
      <article class="card"><p class="label">Current team</p><p class="value">${escapeHtml(overview.current_team || "Not selected")}</p><p class="detail">${state.teams.length} configured</p></article>
      <article class="card"><p class="label">Providers</p><p class="value">${overview.providers.filter(provider => provider.enabled).length} enabled</p><p class="detail">${overview.providers.length} configured entries</p></article>
    </div>
    <div class="section"><div class="section-head"><h2>Latest compact state</h2></div><pre>${escapeHtml(overview.latest_handoff || "No handoff has been recorded.")}</pre></div>
    <div class="section"><div class="section-head"><h2>Controlled work</h2></div>${taskTable(overview.tasks)}</div>`;
  document.getElementById("daemon-card").onclick = () => inspect("Daemon", field("Status", daemon) + field("PID", overview.daemon.pid || "-") + command(`continuum ${overview.daemon.running ? "down" : "up"}`));
  document.getElementById("daemon-card").onclick();
  bindTasks();
}
function renderProjects() {
  const project = state.project;
  document.getElementById("content").innerHTML = `
    <article class="card selectable" id="project-card"><p class="status connected">Observed</p><h2>${escapeHtml(project.name)}</h2><p class="detail mono">${escapeHtml(project.path)}</p>
      <div class="section grid cards"><div><p class="label">Git branch</p><p>${escapeHtml(project.branch || "Not committed")}</p></div><div><p class="label">Memory</p><p>${escapeHtml(project.memory_path)}</p></div><div><p class="label">Obsidian</p><p>${project.obsidian_path ? "Configured" : "Disabled"}</p></div></div>
    </article>`;
  document.getElementById("project-card").onclick = () => inspect("Project", field("Path", project.path) + field("Branch", project.branch || "None") + field("Memory path", project.memory_path) + field("Obsidian path", project.obsidian_path || "Disabled") + command("continuum status"));
  document.getElementById("project-card").onclick();
}
function renderProviders() {
  document.getElementById("content").innerHTML = state.providers.length ? `<div class="grid cards">${state.providers.map(provider => `
    <article class="card selectable provider-card" data-name="${provider.name}"><p class="status ${provider.enabled ? "connected" : "disabled"}">${provider.enabled ? "Enabled" : "Disabled"}</p><h3>${human(provider.name)}</h3><p class="detail">${human(provider.kind)} | ${human(provider.type)}</p><p class="detail">${escapeHtml(provider.model)}</p></article>`).join("")}</div>` :
    `<div class="empty"><h3>No provider config</h3><p>Initialize the project, then enable only providers you choose.</p>${command("continuum init")}</div>`;
  document.querySelectorAll(".provider-card").forEach(card => card.onclick = () => {
    const provider = state.providers.find(item => item.name === card.dataset.name);
    inspect(human(provider.name), field("Type", `${human(provider.kind)} / ${human(provider.type)}`) + field("State", provider.enabled ? "Enabled" : "Disabled") + field("Model", provider.model) + field("Endpoint / command", provider.base_url || provider.command) + `<button id="test-provider" class="button">Test connection</button>` + command(`continuum providers test ${provider.name}`));
    document.getElementById("test-provider").onclick = async () => {
      try { const result = await post("/api/providers/test", {provider: provider.name}); showError(result.result); await loadAll(); }
      catch (error) { showError(error.message); }
    };
  });
  if (state.providers[0]) document.querySelector(".provider-card").click();
}
function renderTeams() {
  const team = state.teams[0];
  if (!team) {
    document.getElementById("content").innerHTML = `<div class="empty"><h3>No teams configured</h3><p>Create the editable default starter team.</p><button id="create-team" class="button">Create Team</button>${command("continuum team init default_dev_team")}</div>`;
    document.getElementById("create-team").onclick = async () => {
      try { await post("/api/teams/create", {preset: "default_dev_team"}); await loadAll(); }
      catch (error) { showError(error.message); }
    };
    inspect("Teams", command("continuum team list"));
    return;
  }
  document.getElementById("content").innerHTML = `<div class="section-head"><div><h2>${human(team.name)}</h2><p class="muted">${human(team.mode)} workflow</p></div></div>
    <div class="grid columns">${Object.entries(team.agents).map(([name, agent]) => `
      <article class="card role-card selectable team-role" data-name="${name}"><p class="role-name">${human(name)}</p><p class="provider">${human(agent.provider)}</p><p class="detail">${human(agent.role)}</p>
      <div class="pills"><span class="pill">${agent.can_edit_files ? "Can edit" : "Read / model"}</span><span class="pill">${agent.can_run_commands ? "Commands" : "No commands"}</span></div></article>`).join("")}</div>
    <div class="section"><div class="section-head"><h2>Run a controlled workflow</h2></div><div class="search"><input id="workflow-request" placeholder="Fix failing auth test"><input id="workflow-files" placeholder="Writable paths, comma-separated"><button id="plan-workflow">Plan</button><button id="execute-workflow">Execute</button></div>${command(`continuum team run ${team.name} "Fix failing auth test" --execute --allow-file src/auth.ts`)}</div>`;
  document.getElementById("plan-workflow").onclick = async () => {
    const request = document.getElementById("workflow-request").value.trim();
    if (!request) return showError("Enter a task before planning.");
    try { const workflow = await post("/api/workflows/run", {team: team.name, request}); showError(`Planned ${workflow.workflow_id}`); await loadAll(); }
    catch (error) { showError(error.message); }
  };
  document.getElementById("execute-workflow").onclick = async () => {
    const request = document.getElementById("workflow-request").value.trim();
    const allowFiles = document.getElementById("workflow-files").value.split(",").map(value => value.trim()).filter(Boolean);
    if (!request) return showError("Enter a task before executing.");
    try { const workflow = await post("/api/workflows/run", {team: team.name, request, execute: true, allow_files: allowFiles}); showError(`Executed ${workflow.workflow_id}`); await loadAll(); }
    catch (error) { showError(error.message); }
  };
  document.querySelectorAll(".team-role").forEach(card => card.onclick = () => {
    const agent = team.agents[card.dataset.name];
    inspect(human(card.dataset.name), field("Provider", human(agent.provider)) + field("Role", human(agent.role)) + field("Can edit files", agent.can_edit_files ? "Yes" : "No") + field("Can run commands", agent.can_run_commands ? "Yes" : "No") + field("One writer safety", team.safety.one_writer_at_a_time ? "Enabled" : "Custom") + `<p class="field-label">Team JSON</p><textarea id="team-json" rows="10">${escapeHtml(JSON.stringify(team, null, 2))}</textarea><button id="save-team" class="button">Save Team</button>`);
    document.getElementById("save-team").onclick = async () => {
      try { await post("/api/teams/save", {name: team.name, config: JSON.parse(document.getElementById("team-json").value)}); showError("Team saved."); await loadAll(); }
      catch (error) { showError(error.message); }
    };
  });
  document.querySelector(".team-role").click();
}
function renderRuns() {
  document.getElementById("content").innerHTML = `
    <div class="section"><div class="section-head"><h2>Workflow Timeline</h2></div>${timelineView()}</div>
    <div class="section"><div class="section-head"><h2>Multi-Agent Worktree Board</h2></div>${worktreeBoardView()}</div>
    <div class="section"><div class="section-head"><h2>Agent Flight Recorder</h2></div>${flightRecordView()}</div>
    <div class="section"><div class="section-head"><h2>Agent ROI</h2></div>${roiView()}</div>
    <div class="section"><div class="section-head"><h2>Context Packet Studio</h2></div>${contextPacketView()}</div>
    <div class="section"><div class="section-head"><h2>Tasks</h2></div>${taskTable(state.tasks)}</div>
    <div class="section"><div class="section-head"><h2>Resume context</h2></div><div class="search"><input id="resume-role" value="coder"><button id="resume-context">Build compact packet</button></div><pre id="resume-output"></pre></div>`;
  bindTasks();
  bindFlightRecords();
  bindContextPackets();
  document.getElementById("resume-context").onclick = async () => {
    try { const result = await post("/api/resume-context", {role: document.getElementById("resume-role").value, mode: "compact"}); document.getElementById("resume-output").textContent = result.text; }
    catch (error) { showError(error.message); }
  };
  inspect("Runs", command("continuum flight-record T0001"));
}
function timelineView() {
  const blocks = state.timeline?.blocks || [];
  if (!blocks.length) return `<div class="empty"><h3>No workflow timeline yet</h3><p>Plan an objective or schedule worktrees to create timeline state.</p>${command('continuum objective "Build X" --mode schedule')}</div>`;
  return `<div class="timeline">${blocks.map(block => `
    <article class="timeline-block">
      <p class="field-label">${escapeHtml(block.lane)}</p>
      <h3>${escapeHtml(block.id)} ${escapeHtml(block.title)}</h3>
      <div class="pills"><span class="pill">${escapeHtml(block.status || "-")}</span><span class="pill">${escapeHtml(block.branch || "no branch")}</span>${block.merge_ready ? '<span class="pill ready">merge ready</span>' : ''}</div>
      <p class="detail">${escapeHtml((block.claimed_files || []).join(", ") || "No claimed files")}</p>
    </article>`).join("")}</div>`;
}
function worktreeBoardView() {
  const schedules = state.worktreeBoard?.schedules || [];
  const standalone = state.worktreeBoard?.standalone || [];
  if (!schedules.length && !standalone.length) return `<div class="empty"><h3>No worktree lanes</h3><p>Create isolated lanes from the terminal.</p>${command('continuum worktree schedule "Build X" --lane backend:claude:src --lane tests:codex:tests')}</div>`;
  const scheduled = schedules.map(schedule => `
    <div class="board-group"><p class="field-label">${escapeHtml(schedule.schedule_id)} ${escapeHtml(schedule.status)}</p><h3>${escapeHtml(schedule.objective)}</h3>
      <div class="worktree-board">${(schedule.lanes || []).map(laneCard).join("")}</div></div>`).join("");
  const solo = standalone.length ? `<div class="board-group"><p class="field-label">Standalone worktrees</p><div class="worktree-board">${standalone.map(laneCard).join("")}</div></div>` : "";
  return scheduled + solo;
}
function laneCard(lane) {
  return `<article class="card lane-card">
    <p class="status ${lane.merge_ready ? "connected" : "disabled"}">${lane.merge_ready ? "Merge ready" : escapeHtml(lane.status || "Active")}</p>
    <h3>${escapeHtml(lane.task_id)} ${escapeHtml(lane.role || "")}</h3>
    <p class="detail">${escapeHtml(lane.agent || "worktree")} | ${escapeHtml(lane.branch || "-")}</p>
    <p class="detail">${escapeHtml((lane.owned_paths || []).join(", ") || "No owned paths")}</p>
    <div class="pills"><span class="pill">tests ${escapeHtml(lane.test_result || "-")}</span><span class="pill">review ${escapeHtml(lane.review_status || "-")}</span></div>
  </article>`;
}
function flightRecordView() {
  if (!state.flightRecords.length) return `<div class="empty"><h3>No flight records</h3><p>Create or assign a task to record auditable agent work.</p>${command('continuum task create "Fix auth"')}</div>`;
  return `<div class="grid cards">${state.flightRecords.map(record => `
    <article class="card selectable flight-card" data-task="${record.task_id}">
      <p class="status ${record.risks.length ? "failed" : "connected"}">${escapeHtml(record.final_status)}</p>
      <h3>${escapeHtml(record.task_id)} ${escapeHtml(record.objective)}</h3>
      <p class="detail">${escapeHtml(record.agent)} | ${escapeHtml(record.branch || "no branch")}</p>
      <p class="detail">${record.files_allowed.length} allowed | ${record.files_touched.length} touched | ${record.risks.length} risks</p>
    </article>`).join("")}</div>`;
}
function contextPacketView() {
  if (!state.contextPackets.length) return `<div class="empty"><h3>No context packets</h3><p>Build task context or schedule a worktree lane.</p>${command("continuum context build coder")}</div>`;
  return `<div class="table"><div class="row header"><span>Task</span><span>Role</span><span>Risk</span><span>Sources</span></div>${state.contextPackets.map(packet => `
    <div class="row selectable context-row" data-task="${packet.task_id}"><span>${escapeHtml(packet.task_id)}</span><span>${escapeHtml(packet.role)}</span><span>${escapeHtml(packet.risk_level || "-")}</span><span>${escapeHtml(packet.source_count ?? "-")}</span></div>`).join("")}</div>`;
}
function roiView() {
  const roi = state.roi || {};
  return `<div class="grid cards">
    <article class="card"><p class="label">Tasks</p><p class="value">${escapeHtml(roi.tasks_completed ?? 0)} done</p><p class="detail">${escapeHtml(roi.tasks_failed ?? 0)} failed | ${escapeHtml(roi.tasks_total ?? 0)} total</p></article>
    <article class="card"><p class="label">Tokens</p><p class="value">${escapeHtml(roi.estimated_tokens ?? 0)}</p><p class="detail">${escapeHtml(roi.cost_per_accepted_change_tokens ?? 0)} per accepted change</p></article>
    <article class="card"><p class="label">Quality</p><p class="value">${escapeHtml(roi.tests_passed ?? 0)} tests</p><p class="detail">${escapeHtml(roi.files_changed_outside_scope ?? 0)} out-of-scope | ${escapeHtml(roi.review_rejections ?? 0)} rejected</p></article>
    <article class="card selectable" id="roi-card"><p class="label">Routing</p><p class="value">${escapeHtml((roi.recommendations || []).length)} rules</p><p class="detail">Cost-aware provider guidance</p></article>
  </div>`;
}
function bindFlightRecords() {
  document.querySelectorAll(".flight-card").forEach(card => card.onclick = async () => {
    try {
      const record = await api(`/api/flight-record?task=${encodeURIComponent(card.dataset.task)}`);
      inspect(record.task_id, field("Objective", record.objective) + field("Agent", record.agent) + field("Final status", record.final_status) +
        field("Files allowed", record.files_allowed.join(", ") || "None") + field("Files touched", record.files_touched.join(", ") || "None") +
        field("Risks", record.risks.join(" | ") || "None") + field("Next action", record.next_action) + command(`continuum flight-record ${record.task_id}`));
    } catch (error) { showError(error.message); }
  });
}
function bindContextPackets() {
  document.querySelectorAll(".context-row").forEach(row => row.onclick = () => {
    const packet = state.contextPackets.find(item => item.task_id === row.dataset.task);
    inspect(`Context ${packet.task_id}`, field("Role", packet.role) + field("Agent", packet.agent || "-") +
      field("Tokens", packet.estimated_tokens ?? "-") + field("Risk", packet.risk_level || "-") +
      field("Missing info", packet.missing_info.join(" | ") || "None") + field("Files", packet.files.join(", ") || "None") +
      command(`continuum context score ${packet.task_id}`));
  });
  const roiCard = document.getElementById("roi-card");
  if (roiCard) roiCard.onclick = () => {
    const roi = state.roi || {};
    inspect("Agent ROI", field("Estimated tokens", roi.estimated_tokens ?? 0) + field("Cost per accepted change", roi.cost_per_accepted_change_tokens ?? 0) +
      field("Manual corrections", roi.manual_corrections ?? 0) + field("Reruns", roi.reruns ?? 0) +
      `<p class="field-label">Recommendations</p><pre>${escapeHtml((roi.recommendations || []).map(item => `${item.task}: ${item.provider} - ${item.reason}`).join("\\n") || "No recommendations.")}</pre>` +
      command("continuum roi"));
  };
}
function renderMemory() {
  document.getElementById("content").innerHTML = `<div class="tabs"><button class="active">Current State</button><button>Decisions</button><button>Implemented</button><button>Open Tasks</button><button>Raw Events</button></div>
    <div class="search"><input id="memory-query" placeholder="Search a file, failure or decision"><button id="memory-search">Search</button></div>
    <div class="section"><pre id="memory-note">${escapeHtml(state.memory.current || "No current context.")}</pre></div><div class="section" id="memory-results"></div>
    <p class="muted section">Tiny handoff first. Relevant memory only when needed. Raw logs only on demand.</p>`;
  const notes = [state.memory.current, state.memory.decisions, state.memory.implemented, state.memory.open_tasks, JSON.stringify(state.memory.results, null, 2)];
  document.querySelectorAll(".tabs button").forEach((button, index) => button.onclick = () => {
    document.querySelectorAll(".tabs button").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById("memory-note").textContent = notes[index] || "No recorded content.";
  });
  document.getElementById("memory-search").onclick = searchMemory;
  inspect("Memory", command('continuum search "topic"'));
}
async function searchMemory() {
  state.memory = await api(`/api/memory?q=${encodeURIComponent(document.getElementById("memory-query").value)}`);
  const results = document.getElementById("memory-results");
  results.innerHTML = state.memory.results.length ? state.memory.results.map(event => `<article class="card"><p class="status connected">${escapeHtml(human(event.kind))}</p><p class="detail mono">${escapeHtml(event.created_at)}</p><p>${escapeHtml(JSON.stringify(event.payload).slice(0, 180))}</p></article>`).join("") : `<div class="empty"><h3>No matching memory</h3><p>Try a narrower query.</p></div>`;
}
function renderHandoffs() {
  document.getElementById("content").innerHTML = `<div class="section"><pre>${escapeHtml(state.memory.handoff || "No handoff recorded.")}</pre></div>`;
  inspect("Handoff", command('continuum handoff --task "<current task>" --next-step "<next action>"'));
}
function renderSettings() {
  document.getElementById("content").innerHTML = `<div class="grid cards">
    <article class="card"><p class="label">Privacy</p><p class="value">Local-first</p><p class="detail">State remains under .continuum/</p></article>
    <article class="card"><p class="label">Session context limit</p><p class="value">${escapeHtml(state.project.context_limit || "Not initialized")}</p><p class="detail">Checkpoint threshold ${escapeHtml(state.project.threshold || "-")}</p></article>
    <article class="card"><p class="label">Control Center</p><p class="value">Explicit controls</p><p class="detail">Actions mirror audited CLI operations</p></article></div>`;
  inspect("Settings", field("Project", state.project.path) + field("Memory path", state.project.memory_path) + field("Obsidian mirror", state.project.obsidian_path || "Disabled") + command("continuum doctor"));
}
function renderActivity() {
  const latest = state.events[0];
  document.getElementById("activity-summary").textContent = latest ? `${latest.kind} | ${latest.created_at}` : "No recorded activity";
  document.getElementById("activity-log").innerHTML = state.events.map(event => `<div class="event"><time>${escapeHtml(event.created_at)}</time><span class="kind">${escapeHtml(event.kind)}</span><span>${escapeHtml(JSON.stringify(event.payload).slice(0, 180))}</span></div>`).join("");
}

document.querySelectorAll("#nav button").forEach(button => button.onclick = () => { state.view = button.dataset.view; render(); });
document.getElementById("activity-toggle").onclick = () => document.getElementById("activity").classList.toggle("collapsed");
loadAll().catch(error => showError(error.message));
setInterval(() => api("/api/events").then(events => { state.events = events; renderActivity(); }).catch(() => {}), 5000);
