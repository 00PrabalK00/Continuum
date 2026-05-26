#!/usr/bin/env node
"use strict";

const path = require("path");
const { spawnSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const env = { ...process.env };
env.PYTHONPATH = env.PYTHONPATH ? `${root}${path.delimiter}${env.PYTHONPATH}` : root;
env.PYTHONDONTWRITEBYTECODE = "1";
const forwarded = process.argv.slice(2);
const candidates = process.platform === "win32"
  ? [["py", ["-3", "-m", "continuum"]], ["python", ["-m", "continuum"]]]
  : [["python3", ["-m", "continuum"]], ["python", ["-m", "continuum"]]];

for (const [program, prefix] of candidates) {
  const result = spawnSync(program, [...prefix, ...forwarded], { stdio: "inherit", env });
  if (!result.error || result.error.code !== "ENOENT") {
    process.exit(result.status === null ? 1 : result.status);
  }
}

console.error("Continuum requires Python 3.9 or newer on PATH.");
process.exit(1);
