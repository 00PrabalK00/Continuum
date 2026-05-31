"""Deterministic command risk classification for Continuum (`§Phase3.10`).

`classify(command_str)` maps a shell command to a coarse category, a risk level
and the literal signal that matched. The rules are intentionally conservative:
when nothing matches with confidence the command is reported as `unknown`/`low`
rather than guessing a category. This engine never executes the command; it only
inspects the string so it can gate scripts or flag approval-required actions.

Categories:
    read_only          Inspecting state (ls, cat, git status, grep).
    build              Compiling/building (make, cargo build, npm run build).
    test               Running tests (pytest, unittest, go test, npm test).
    file_write         Mutating files in place (>, tee, sed -i, mv, cp, touch).
    network            Transferring data over the network (curl, wget, nc, scp).
    package_install    Installing third-party packages (pip/npm/cargo install).
    destructive        Irreversible data loss (rm -rf, del, format, mkfs, dd).
    credential_access  Reading secrets/credentials (cat ~/.ssh/*, env, .env).
    unknown            No confident match.

Risk levels: "low", "med", "high". `destructive` and `credential_access` are
always "high" so they can gate scripts (CLI exits non-zero) and be flagged as
approval-required by the governance policy.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORIES = {
    "read_only",
    "build",
    "test",
    "file_write",
    "network",
    "package_install",
    "destructive",
    "credential_access",
    "unknown",
}
RISK_LEVELS = ("low", "med", "high")

# Ordered, conservative rules. The FIRST rule whose pattern matches wins, so the
# most dangerous and most specific signals are listed first. Each rule is
# (category, risk, compiled_pattern, human_signal). Patterns are matched
# case-insensitively against the raw command string.
_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    # --- destructive (high): irreversible data loss -------------------------
    ("destructive", "high", re.compile(r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf]", re.I), "rm with recursive/force flag"),
    ("destructive", "high", re.compile(r"\brm\s+-[a-z]*r", re.I), "rm recursive"),
    ("destructive", "high", re.compile(r"(?:^|[\s;|&])del\s", re.I), "del"),
    ("destructive", "high", re.compile(r"\b(?:Remove-Item)\b.*-Recurse", re.I), "Remove-Item -Recurse"),
    ("destructive", "high", re.compile(r"\brmdir\s+/s", re.I), "rmdir /s"),
    ("destructive", "high", re.compile(r"\bmkfs(?:\.\w+)?\b", re.I), "mkfs"),
    ("destructive", "high", re.compile(r"\bformat\b", re.I), "format"),
    ("destructive", "high", re.compile(r"\bdd\b.*\bof=", re.I), "dd of="),
    ("destructive", "high", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:", re.I), "fork bomb"),
    ("destructive", "high", re.compile(r"\bgit\s+(?:reset\s+--hard|clean\s+-[a-z]*[fd])", re.I), "git reset --hard / clean -fd"),
    ("destructive", "high", re.compile(r"\bgit\s+push\b.*(?:--force|-f)\b", re.I), "git push --force"),
    ("destructive", "high", re.compile(r"\btruncate\b", re.I), "truncate"),
    ("destructive", "high", re.compile(r"\bshred\b", re.I), "shred"),
    # --- credential_access (high): reading secrets --------------------------
    ("credential_access", "high", re.compile(r"~?[\\/]?\.ssh\b", re.I), "ssh key/config access"),
    ("credential_access", "high", re.compile(r"~?[\\/]?\.aws[\\/]credentials", re.I), "aws credentials"),
    ("credential_access", "high", re.compile(r"\.netrc\b", re.I), ".netrc access"),
    ("credential_access", "high", re.compile(r"(?:^|[\s;|&/\\])(?:\.env)(?:\.\w+)?\b", re.I), ".env file access"),
    ("credential_access", "high", re.compile(r"\b(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)\b", re.I), "private key file"),
    ("credential_access", "high", re.compile(r"\b(?:printenv|env)\b(?!\s*[A-Za-z_])", re.I), "dump environment variables"),
    ("credential_access", "high", re.compile(r"\bGet-ChildItem\s+Env:", re.I), "dump environment (PowerShell)"),
    ("credential_access", "high", re.compile(r"(?:^|[\s;|&/\\])(?:credentials|secrets?)(?:[\\/.]|$)", re.I), "credentials/secrets path"),
    # --- package_install (med): installing third-party code -----------------
    ("package_install", "med", re.compile(r"\b(?:pip|pip3)\s+install\b", re.I), "pip install"),
    ("package_install", "med", re.compile(r"\bpython\s+-m\s+pip\s+install\b", re.I), "python -m pip install"),
    ("package_install", "med", re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:install|add|i)\b", re.I), "npm/yarn/pnpm install"),
    ("package_install", "med", re.compile(r"\bcargo\s+install\b", re.I), "cargo install"),
    ("package_install", "med", re.compile(r"\bgo\s+install\b", re.I), "go install"),
    ("package_install", "med", re.compile(r"\bgem\s+install\b", re.I), "gem install"),
    ("package_install", "med", re.compile(r"\b(?:apt|apt-get|yum|dnf|brew|choco|winget|pacman)\s+install\b", re.I), "system package install"),
    ("package_install", "med", re.compile(r"\b(?:apt-get|apt)\s+install\b", re.I), "apt install"),
    # --- network (med): data leaving/entering over the network --------------
    ("network", "med", re.compile(r"\b(?:curl|wget)\b", re.I), "curl/wget"),
    ("network", "med", re.compile(r"\b(?:nc|ncat|netcat)\b", re.I), "netcat"),
    ("network", "med", re.compile(r"\b(?:scp|sftp|rsync)\b", re.I), "scp/sftp/rsync"),
    ("network", "med", re.compile(r"\bssh\b", re.I), "ssh"),
    ("network", "med", re.compile(r"\btelnet\b", re.I), "telnet"),
    ("network", "med", re.compile(r"\bInvoke-WebRequest\b|\bInvoke-RestMethod\b|\bwget\b|\bcurl\b", re.I), "Invoke-WebRequest/RestMethod"),
    ("network", "med", re.compile(r"\bftp\b", re.I), "ftp"),
    # --- test (low): running tests ------------------------------------------
    ("test", "low", re.compile(r"\bpytest\b", re.I), "pytest"),
    ("test", "low", re.compile(r"-m\s+unittest\b", re.I), "python -m unittest"),
    ("test", "low", re.compile(r"-m\s+pytest\b", re.I), "python -m pytest"),
    ("test", "low", re.compile(r"\bgo\s+test\b", re.I), "go test"),
    ("test", "low", re.compile(r"\bcargo\s+test\b", re.I), "cargo test"),
    ("test", "low", re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b", re.I), "npm/yarn test"),
    ("test", "low", re.compile(r"\b(?:tox|nox|jest|mocha|vitest|rspec|phpunit)\b", re.I), "test runner"),
    # --- build (low): compiling/building ------------------------------------
    ("build", "low", re.compile(r"\bmake\b", re.I), "make"),
    ("build", "low", re.compile(r"\bcargo\s+build\b", re.I), "cargo build"),
    ("build", "low", re.compile(r"\bgo\s+build\b", re.I), "go build"),
    ("build", "low", re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?build\b", re.I), "npm/yarn build"),
    ("build", "low", re.compile(r"\b(?:gcc|g\+\+|clang|cmake|gradle|mvn|maven|tsc|webpack|setup\.py)\b", re.I), "compiler/build tool"),
    ("build", "low", re.compile(r"\bdocker\s+build\b", re.I), "docker build"),
    # --- file_write (med): mutating files in place --------------------------
    ("file_write", "med", re.compile(r"\bsed\s+-i\b", re.I), "sed in-place"),
    ("file_write", "med", re.compile(r"\btee\b", re.I), "tee"),
    ("file_write", "med", re.compile(r">>?(?!\s*&?\d)", re.I), "output redirection"),
    ("file_write", "med", re.compile(r"\b(?:mv|move)\b", re.I), "move file"),
    ("file_write", "med", re.compile(r"\b(?:cp|copy)\b", re.I), "copy file"),
    ("file_write", "med", re.compile(r"\b(?:touch|mkdir)\b", re.I), "touch/mkdir"),
    ("file_write", "med", re.compile(r"\b(?:Set-Content|Add-Content|Out-File|New-Item)\b", re.I), "PowerShell write"),
    ("file_write", "med", re.compile(r"\bchmod\b|\bchown\b|\bicacls\b", re.I), "permission change"),
    # --- read_only (low): inspecting state ----------------------------------
    ("read_only", "low", re.compile(r"\bgit\s+(?:status|log|diff|show|branch|remote|fetch)\b", re.I), "git read command"),
    ("read_only", "low", re.compile(r"(?:^|[\s;|&])(?:ls|dir|pwd|cd|whoami|date|uptime|hostname)\b", re.I), "shell inspect command"),
    ("read_only", "low", re.compile(r"(?:^|[\s;|&])(?:cat|less|more|head|tail|type)\b", re.I), "read file"),
    ("read_only", "low", re.compile(r"\b(?:grep|rg|ripgrep|ack|find|fd|which|where)\b", re.I), "search command"),
    ("read_only", "low", re.compile(r"\b(?:echo|printf)\b", re.I), "echo"),
    ("read_only", "low", re.compile(r"\b(?:Get-ChildItem|Get-Content|Select-String|Get-Location|Test-Path)\b", re.I), "PowerShell read"),
    ("read_only", "low", re.compile(r"\bgit\s+status\b", re.I), "git status"),
]


def classify(command_str: str) -> dict[str, Any]:
    """Classify one command string deterministically.

    Returns a dict with `category`, `level`, `signal` and the original
    `command`. Empty/whitespace input and anything with no confident match is
    `unknown`/`low` with `signal=None` (prefer a false "unknown" over a
    wrong-confident category).
    """
    command = (command_str or "").strip()
    if not command:
        return {"command": "", "category": "unknown", "level": "low", "signal": None}
    for category, level, pattern, signal in _RULES:
        if pattern.search(command):
            return {"command": command, "category": category, "level": level, "signal": signal}
    return {"command": command, "category": "unknown", "level": "low", "signal": None}


def render(result: dict[str, Any]) -> str:
    """Human-readable one-block rendering of a classification result."""
    lines = [
        f"Command: {result['command'] or '(empty)'}",
        f"Category: {result['category']}",
        f"Risk level: {result['level']}",
        f"Matched signal: {result['signal'] or 'none (no confident rule matched)'}",
    ]
    return "\n".join(lines)
