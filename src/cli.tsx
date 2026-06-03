#!/usr/bin/env bun
import React, {useMemo, useState} from "react";
import {Box, render, Text, useApp, useInput} from "ink";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {type Agent, runContinuum, translateInput} from "./continuum-runtime.ts";

type Entry = {
  kind: "system" | "input" | "output" | "error";
  text: string;
};

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const shellArgs = parseShellArgs(process.argv.slice(2));

function App() {
  const {exit} = useApp();
  const [agent, setAgent] = useState<Agent>(shellArgs.agent);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [entries, setEntries] = useState<Entry[]>([
    {kind: "system", text: "Continuum shell. Type /help for commands, /quit to exit."},
  ]);

  const columns = process.stdout.columns || 100;
  const promptWidth = useMemo(() => {
    // Ink renders through Yoga; keeping the prompt width stable prevents input jitter.
    return Math.max(12, Math.min(24, Math.floor(columns * 0.22)));
  }, [columns]);

  useInput((value, key) => {
    if (busy) {
      return;
    }
    if (key.return) {
      void submit();
      return;
    }
    if (key.backspace || key.delete) {
      setInput((current) => current.slice(0, -1));
      return;
    }
    if (key.ctrl && value === "c") {
      exit();
      return;
    }
    if (value) {
      setInput((current) => current + value);
    }
  });

  async function submit() {
    const command = input.trim();
    setInput("");
    if (!command) {
      return;
    }
    setEntries((current) => [...current, {kind: "input", text: command}]);
    const translated = translateInput(command, agent, {project: shellArgs.project, vault: shellArgs.vault, root});
    if (!Array.isArray(translated)) {
      handleLocal(translated.local);
      return;
    }
    setBusy(true);
    const result = await runContinuum(translated, {project: shellArgs.project, vault: shellArgs.vault, root});
    setEntries((current) => [
      ...current,
      {kind: result.code === 0 ? "output" : "error", text: result.output.trim() || `(exit ${result.code})`},
    ]);
    setBusy(false);
  }

  function handleLocal(action: string) {
    if (action === "quit") {
      exit();
      return;
    }
    if (action.startsWith("agent:")) {
      const next = action.split(":")[1] as Agent;
      setAgent(next);
      setEntries((current) => [...current, {kind: "system", text: `Selected agent: ${next}`}]);
      return;
    }
    if (action.startsWith("switch:")) {
      const [, next, mode] = action.split(":") as ["switch", Agent, string];
      setAgent(next);
      setEntries((current) => [...current, {kind: "system", text: `Selected agent: ${next}; run /resume ${next} ${mode} to inject context.`}]);
      return;
    }
    setEntries((current) => [...current, {kind: "system", text: helpText(action)}]);
  }

  return (
    <Box flexDirection="column">
      <Box borderStyle="round" borderColor="cyan" paddingX={1} flexDirection="column">
        <Text bold>Continuum</Text>
        <Text color="gray">Project: {shellArgs.project}</Text>
        <Text color="gray">Runtime: TypeScript + React Ink, Yoga layout, Bun-first</Text>
      </Box>
      <Box marginTop={1} flexDirection="column">
        {entries.slice(-12).map((entry, index) => (
          <Text key={`${index}-${entry.kind}`} color={colorFor(entry.kind)}>
            {prefixFor(entry.kind)} {entry.text}
          </Text>
        ))}
      </Box>
      <Box marginTop={1}>
        <Box width={promptWidth}>
          <Text color={agentColor(agent)}>continuum[{agent}]&gt;</Text>
        </Box>
        <Text>{input}</Text>
        {busy ? <Text color="yellow"> running</Text> : null}
      </Box>
    </Box>
  );
}

function colorFor(kind: Entry["kind"]) {
  if (kind === "error") return "red";
  if (kind === "input") return "cyan";
  if (kind === "system") return "gray";
  return undefined;
}

function prefixFor(kind: Entry["kind"]) {
  if (kind === "input") return ">";
  if (kind === "error") return "!";
  return "-";
}

function agentColor(agent: Agent) {
  if (agent === "claude") return "magenta";
  if (agent === "gemini") return "blue";
  return "cyan";
}

function helpText(action: string) {
  if (action.endsWith("-help")) {
    return `Usage: /${action.replace("-help", "")} <subcommand> [arguments]`;
  }
  if (action === "agent-help") {
    return "Usage: /agent claude|codex|gemini";
  }
  if (action === "switch-help") {
    return "Usage: /switch claude|codex|gemini [compact|normal|deep]";
  }
  if (action === "unknown") {
    return "Unknown command. Type /help.";
  }
  return [
    "Commands: /status, /doctor, /up, /down, /handoff task | next step, /agent, /chat, /terminal, /resume-terminal, /memory, /plan, /task, /team, /worktree, /ui, /quit.",
    "Plain text is sent to the selected agent with compact Continuum context.",
  ].join("\n");
}

function parseShellArgs(argv: string[]): {project: string; vault?: string; agent: Agent} {
  let project = process.cwd();
  let vault: string | undefined;
  let agent: Agent = "codex";
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--project" && argv[index + 1]) {
      project = path.resolve(argv[index + 1]);
      index += 1;
    } else if (value.startsWith("--project=")) {
      project = path.resolve(value.slice("--project=".length));
    } else if (value === "--vault" && argv[index + 1]) {
      vault = path.resolve(argv[index + 1]);
      index += 1;
    } else if (value.startsWith("--vault=")) {
      vault = path.resolve(value.slice("--vault=".length));
    } else if (value === "--agent" && isAgent(argv[index + 1])) {
      agent = argv[index + 1] as Agent;
      index += 1;
    } else if (value.startsWith("--agent=") && isAgent(value.slice("--agent=".length))) {
      agent = value.slice("--agent=".length) as Agent;
    }
  }
  return {project, vault, agent};
}

function isAgent(value: string | undefined): value is Agent {
  return value === "claude" || value === "codex" || value === "gemini";
}

render(<App />);
