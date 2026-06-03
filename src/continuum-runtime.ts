import path from "node:path";
import {spawn, spawnSync} from "node:child_process";

export type Agent = "claude" | "codex" | "gemini";

export type RuntimeOptions = {
  project: string;
  vault?: string;
  root: string;
};

export type CommandResult = {
  code: number;
  output: string;
};

const topLevelCommon = new Set([
  "init",
  "daemon",
  "up",
  "down",
  "logs",
  "handoff",
  "run",
  "resume",
  "status",
  "doctor",
  "search",
  "service",
  "autostart",
  "mcp",
  "ui",
  "shell",
  "instruct",
  "chat",
]);

const nestedCommon = new Set([
  "session",
  "adapters",
  "task",
  "providers",
  "model",
  "memory",
  "context",
  "message",
  "team",
  "worktree",
  "route",
]);

const agents = new Set<Agent>(["claude", "codex", "gemini"]);

export function commonArgs(options: RuntimeOptions): string[] {
  const args = ["--project", path.resolve(options.project)];
  if (options.vault) {
    args.push("--vault", path.resolve(options.vault));
  }
  return args;
}

export function pythonPrefix(): [string, string[]] {
  const candidates: [string, string[]][] = process.platform === "win32"
    ? [["py", ["-3", "-m", "continuum"]], ["python", ["-m", "continuum"]]]
    : [["python3", ["-m", "continuum"]], ["python", ["-m", "continuum"]]];
  for (const candidate of candidates) {
    const result = spawnSync(candidate[0], [...candidate[1], "--version"], {
      env: pythonEnv(process.cwd()),
      stdio: "ignore",
    });
    if (!result.error) {
      return candidate;
    }
  }
  return candidates[candidates.length - 1];
}

export function pythonEnv(root: string): NodeJS.ProcessEnv {
  return {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH ? `${root}${path.delimiter}${process.env.PYTHONPATH}` : root,
    PYTHONDONTWRITEBYTECODE: "1",
  };
}

export function runContinuum(argv: string[], options: RuntimeOptions): Promise<CommandResult> {
  const [program, prefix] = pythonPrefix();
  return new Promise((resolve) => {
    const child = spawn(program, [...prefix, ...argv], {
      cwd: options.project,
      env: pythonEnv(options.root),
      shell: false,
    });
    let output = "";
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      resolve({code: 1, output: String(error.message)});
    });
    child.on("close", (code) => {
      resolve({code: code ?? 1, output});
    });
  });
}

export function translateInput(input: string, agent: Agent, options: RuntimeOptions): string[] | {local: string} {
  const line = input.trim();
  const common = commonArgs(options);
  if (!line || line === "/" || line === "/help" || line === "help" || line === "?") {
    return {local: "help"};
  }
  if (line === "/quit" || line === "/exit" || line === "quit" || line === "exit") {
    return {local: "quit"};
  }
  if (!line.startsWith("/")) {
    return ["chat", ...common, agent, "compact", line];
  }
  const parts = splitCommand(line.slice(1));
  const command = parts[0]?.toLowerCase();
  const rest = parts.slice(1);
  if (!command) {
    return {local: "help"};
  }
  if (command === "agent") {
    const next = rest[0];
    return next && agents.has(next as Agent) ? {local: `agent:${next}`} : {local: "agent-help"};
  }
  if (command === "status" || command === "doctor" || command === "up" || command === "down" || command === "logs") {
    return [command, ...common, ...rest];
  }
  if (command === "search") {
    return ["search", ...common, rest.join(" ")];
  }
  if (command === "handoff" && rest.includes("|")) {
    const divider = rest.indexOf("|");
    return ["handoff", ...common, "--task", rest.slice(0, divider).join(" "), "--next-step", rest.slice(divider + 1).join(" ")];
  }
  if (command === "chat") {
    const [target, mode, body] = agentModeBody(rest, agent);
    return ["chat", ...common, target, mode, body];
  }
  if (command === "switch") {
    const next = rest[0];
    if (!next || !agents.has(next as Agent)) {
      return {local: "switch-help"};
    }
    const mode = rest[1] && ["compact", "normal", "deep"].includes(rest[1]) ? rest[1] : "compact";
    return {local: `switch:${next}:${mode}`};
  }
  if (command === "terminal" || command === "pty") {
    const [target, passthrough] = agentAndRest(rest, agent);
    return ["run", ...common, "--interactive", target, ...approvalArgs(target), ...passthrough];
  }
  if (command === "resume-terminal" || command === "resume-pty") {
    const [target, passthrough] = agentAndRest(rest, agent);
    const mode = passthrough[0] && ["compact", "normal", "deep"].includes(passthrough[0]) ? passthrough.shift()! : "compact";
    return ["resume", ...common, "--interactive", target, mode, ...approvalArgs(target), ...passthrough];
  }
  if (command === "memory") {
    const semantic = rest.includes("--semantic") ? ["--semantic"] : [];
    const query = rest.filter((item) => item !== "--semantic").join(" ");
    return ["memory", "retrieve", ...common, query, ...semantic];
  }
  if (command === "plan") {
    return ["team", "run", ...common, "default_dev_team", rest.join(" ")];
  }
  if (topLevelCommon.has(command)) {
    return injectCommon([command, ...rest], 1, common);
  }
  if (nestedCommon.has(command)) {
    return rest.length ? injectCommon([command, ...rest], 2, common) : {local: `${command}-help`};
  }
  return {local: "unknown"};
}

function splitCommand(value: string): string[] {
  const matches = value.match(/"([^"]*)"|'([^']*)'|\S+/g) ?? [];
  return matches.map((item) => item.replace(/^["']|["']$/g, ""));
}

function injectCommon(argv: string[], index: number, common: string[]): string[] {
  if (argv.some((item) => item === "--project" || item.startsWith("--project="))) {
    return argv;
  }
  return [...argv.slice(0, index), ...common, ...argv.slice(index)];
}

function agentAndRest(values: string[], fallback: Agent): [Agent, string[]] {
  const first = values[0];
  if (first && agents.has(first as Agent)) {
    return [first as Agent, values.slice(1)];
  }
  return [fallback, [...values]];
}

function agentModeBody(values: string[], fallback: Agent): [Agent, string, string] {
  const [target, rest] = agentAndRest(values, fallback);
  const mode = rest[0] && ["compact", "normal", "deep"].includes(rest[0]) ? rest.shift()! : "compact";
  return [target, mode, rest.join(" ")];
}

function approvalArgs(agent: Agent): string[] {
  if (agent === "codex") {
    return ["--ask-for-approval", "on-request"];
  }
  if (agent === "gemini") {
    return ["--approval-mode", "default"];
  }
  return ["--permission-mode", "default"];
}
