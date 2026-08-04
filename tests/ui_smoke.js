/* Render every view against fixture data, with no browser.
 *
 * `node --check` proves app.js parses, which is not the same as proving it
 * renders. This drives all four views through a minimal DOM and fails on any
 * thrown error, any unhandled rejection, and on a view that produces nothing.
 *
 * Run: node tests/ui_smoke.js
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "continuum", "ui", "app.js");

const FIXTURES = {
  "/api/now": {
    project: "demo", branch: "main",
    task: "renamed the payment client to BillingGateway",
    next_step: "fix the failing retry test",
    age_days: 37, drift: "Recorded against commit 7bab8ee, 14 commits ago.",
    recorded_against: "7bab8ee",
    decisions: ["chose PostgreSQL over MySQL"],
    open_questions: ["the retry test fails on the timeout"],
    facts: ["the retry test asserts 3 attempts"],
  },
  "/api/checkpoints": [
    { id: 9, ref: "C9", created_at: "2026-08-04T06:08:00+00:00", task: "migrated the callers", next_step: "run the suite", commit: "5336440", source: "cli", branch: "main" },
    { id: 2, ref: "C2", created_at: "2026-08-03T06:08:00+00:00", task: "renamed the payment client", next_step: "migrate callers", commit: "ed01978", source: "cli", branch: "main" },
  ],
  "/api/branches": [
    { name: "main", current: true, task: "migrated the callers", checkpoints: 2 },
    { name: "codex-lane", current: false, task: "renamed it to LedgerClient", checkpoints: 1 },
  ],
  "/api/notes": [
    { id: 3, type: "fact", text: "the retry test asserts 3 attempts", state: "", created_at: "2026-08-04T06:08:00+00:00" },
    { id: 2, type: "hypothesis", text: "it fails on the timeout", state: "open", created_at: "2026-08-04T06:07:00+00:00" },
    { id: 1, type: "decision", text: "chose PostgreSQL over MySQL", state: "", created_at: "2026-08-04T06:06:00+00:00" },
  ],
  "/api/providers": [{ name: "ollama", kind: "model", type: "local" }],
  "/api/teams": [{ name: "default_dev_team", roles: ["coder", "tester"] }],
  "/api/tasks": [{ task_id: "T0001", title: "Fix auth", status: "RUNNING" }],
  "/api/events": [{ id: 1, kind: "handoff", created_at: "2026-08-04T06:08:00+00:00" }],
  "/api/diff": { older: "C2", newer: "C9", diff: "C2 -> C9\n\nTask\n- renamed\n+ migrated" },
  "/api/blame": { query: "BillingGateway", summary: "'BillingGateway' first recorded in C2.", matches: [] },
};

const failures = [];
const listeners = {};

function makeNode(id) {
  const node = {
    id,
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    style: {},
    value: "",
    _html: "",
    get innerHTML() { return this._html; },
    set innerHTML(value) { this._html = String(value); },
    set textContent(value) { this._text = String(value); },
    get textContent() { return this._text || ""; },
    addEventListener(event, handler) { (listeners[id] ||= {})[event] = handler; },
    closest() { return null; },
    querySelectorAll() { return []; },
  };
  return node;
}

const nodes = { view: makeNode("view"), toast: makeNode("toast"), tabs: makeNode("tabs"), project: makeNode("project") };

const context = {
  console,
  setTimeout,
  clearTimeout,
  encodeURIComponent,
  Promise,
  document: {
    /* A browser returns null for an id that is not on the page. Handing back a
       fresh stub node instead made every getElementById succeed, which hid the
       bug this test exists to catch: the History view wired controls that the
       empty state never renders. */
    getElementById(id) {
      if (nodes[id]) return nodes[id];
      /* Not cached. A node found in one render must not still resolve in the
         next one, or the empty state inherits the controls the populated view
         happened to draw, which is exactly the bug being tested for. */
      if (String(nodes.view.innerHTML).includes(`id="${id}"`)) return makeNode(id);
      return null;
    },
    querySelectorAll: () => [],
  },
  location: { hash: "" },
  window: { addEventListener() {} },
  async fetch(url) {
    const key = String(url).split("?")[0];
    if (!(key in FIXTURES)) return { ok: false, status: 404, json: async () => ({ error: "Unknown endpoint" }) };
    return { ok: true, status: 200, json: async () => FIXTURES[key] };
  },
};
context.globalThis = context;

process.on("unhandledRejection", (reason) => failures.push(`unhandled rejection: ${reason}`));

vm.createContext(context);
try {
  vm.runInContext(fs.readFileSync(APP, "utf8"), context, { filename: "app.js" });
} catch (error) {
  failures.push(`app.js threw on load: ${error.message}`);
}

const EXPECT = {
  now: ["BillingGateway", "fix the failing retry test", "37 days ago", "14 commits ago", "chose PostgreSQL", "open"],
  history: ["C9", "migrated the callers", "codex-lane", "Find where a claim came from"],
  notes: ["decision", "hypothesis", "fact", "open"],
  advanced: ["ollama", "default_dev_team", "T0001", "orchestration"],
};

function finish() {
  if (failures.length) {
    console.error("\nFAILED:");
    failures.forEach((line) => console.error("  " + line));
    process.exit(1);
  }
  console.log("\nall views render");
  process.exit(0);
}

/* Anything thrown past here must fail the run. An earlier version let a
   TypeError escape the async body, which skipped the reporting entirely and
   exited 0 against a completely broken app.js. */
process.on("uncaughtException", (error) => { failures.push(`threw: ${error.message}`); finish(); });

(async () => {
  if (typeof context.load !== "function") {
    failures.push("load() is not exposed; app.js did not define the view loader");
    finish();
  }
  for (const view of Object.keys(EXPECT)) {
    await context.load(view);
    const html = nodes.view.innerHTML;
    if (!html || html.length < 60) {
      failures.push(`${view}: rendered nothing`);
      continue;
    }
    if (/Could not load this view/.test(html)) {
      failures.push(`${view}: rendered its error state`);
      continue;
    }
    for (const needle of EXPECT[view]) {
      if (!html.includes(needle)) failures.push(`${view}: missing ${JSON.stringify(needle)}`);
    }
    if (/undefined|\[object Object\]|NaN/.test(html)) {
      failures.push(`${view}: rendered a placeholder value`);
    }
    console.log(`${view.padEnd(9)} rendered ${String(html.length).padStart(5)} chars`);
  }

  /* A brand new project. Every view must render its empty state rather than an
     error: the first version wired the blame controls unconditionally, so the
     History tab threw and showed "Could not load this view" until the first
     checkpoint existed. Fixtures with data never reach that path, which is why
     it survived. */
  const EMPTY = {
    "/api/now": { project: "fresh", branch: "main", task: "", next_step: "", age_days: null,
                  drift: null, recorded_against: "", decisions: [], open_questions: [], facts: [] },
    "/api/checkpoints": [], "/api/branches": [], "/api/notes": [],
    "/api/providers": [], "/api/teams": [], "/api/tasks": [], "/api/events": [],
  };
  Object.assign(FIXTURES, EMPTY);
  for (const view of Object.keys(EXPECT)) {
    await context.load(view);
    const html = nodes.view.innerHTML;
    if (/Could not load this view/.test(html)) failures.push(`${view}: empty project renders an error`);
    if (!/empty-state/.test(html)) failures.push(`${view}: empty project renders no guidance`);
  }
  console.log("empty     all four views render their empty state");

  // Recorded text is never trusted markup. Tested through the rendered output
  // rather than by calling esc() directly: esc is a const, so it never reaches
  // the VM context, and a check that reads context.esc silently passes without
  // ever running.
  const HOSTILE = '<img src=x onerror="alert(1)">';
  FIXTURES["/api/now"].task = HOSTILE;
  await context.load("now");
  const rendered = nodes.view.innerHTML;
  if (rendered.includes("<img src=x")) failures.push("recorded text is rendered as markup");
  if (!rendered.includes("&lt;img")) failures.push("recorded text is not escaped");

  if (failures.length) {
    console.error("\nFAILED:");
    failures.forEach((line) => console.error("  " + line));
    process.exit(1);
  }
  console.log("\nall views render");
})();
