"""Symbol-aware context intelligence for Continuum (Strategy Gap3 / Phase2).

These pure functions turn the context a task receives into an *inspectable*
object rather than a hidden prompt. Three capabilities sit on top of the
existing `MemoryStore`:

- `gather_context_intel` aggregates the files, symbols, tests, recent commits,
  prior decisions and blockers relevant to a task (or an explicit file set).
- `diff_intel` compares two such records and reports what each side received
  and what differs ("who got stale context").
- `score_intel` scores a built packet: token size, source count, staleness,
  a deterministic risk level and a missing-information checklist.

Everything is best-effort and stdlib + git subprocess only. Symbol extraction
is a conservative language-agnostic regex over file text; token estimates reuse
the repo's `chars // 4` heuristic via `core.estimate_tokens`. Nothing here
trusts an agent self-report -- every field is read back from the project tree,
the recorded event log, file claims and git.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .core import (
    IGNORE_DIRS,
    MemoryStore,
    compact_text,
    estimate_tokens,
    utc_now,
)

# Conservative, language-agnostic symbol patterns. Each captures the symbol name
# in group "name" and is matched line-by-line against source text. The goal is a
# useful best-effort list, not a parser: false negatives are fine, and we filter
# obvious noise (comments / string-only lines) before matching.
_SYMBOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Python / similar: def foo(...), async def foo(...)
    ("def", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    # class Foo
    ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_]\w*)")),
    # JS/TS: function foo(...), export function foo(...), export default function foo
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")),
    # exported / module-level const/let/var bindings (incl. arrow funcs)
    ("const", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=")),
    # Python module-level UPPER_CASE constants: NAME = ... at column 0.
    ("const", re.compile(r"^(?P<name>[A-Z_][A-Z0-9_]{2,})\s*[:=]")),
    # Go / Rust / C-family: func Foo(...), fn foo(...)
    ("func", re.compile(r"^\s*(?:pub\s+)?(?:func|fn)\s+(?P<name>[A-Za-z_]\w*)\s*[\(<]")),
    # interfaces / structs / types (TS, Go, Rust)
    ("type", re.compile(r"^\s*(?:export\s+)?(?:interface|struct|type|enum)\s+(?P<name>[A-Za-z_]\w*)")),
)

# Lines that are obviously comments or whole-line strings: skip before matching so
# a commented-out `def` or a docstring line does not register as a symbol.
_COMMENT_PREFIXES = ("#", "//", "*", "/*", '"""', "'''", '"', "'")

_SOURCE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".swift", ".scala",
}

_MAX_SYMBOLS = 60
_MAX_FILE_BYTES = 400_000


def _looks_like_comment_or_string(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return stripped.startswith(_COMMENT_PREFIXES)


def extract_symbols(source: str | Path) -> list[dict[str, str]]:
    """Extract top-level-ish defs/classes/functions/exported consts from text.

    Accepts either source text or a file `Path`. Returns a de-duplicated list of
    ``{"kind": ..., "name": ...}`` in first-seen order. Conservative by design:
    comment and whole-line-string lines are skipped, and matching is regex-only
    (no real parsing), so this is a best-effort signal across common languages.
    """
    if isinstance(source, Path):
        try:
            if source.stat().st_size > _MAX_FILE_BYTES:
                return []
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    else:
        text = source or ""

    seen: set[tuple[str, str]] = set()
    symbols: list[dict[str, str]] = []
    for line in text.splitlines():
        if _looks_like_comment_or_string(line):
            continue
        for kind, pattern in _SYMBOL_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group("name")
            key = (kind, name)
            if key in seen:
                continue
            seen.add(key)
            symbols.append({"kind": kind, "name": name})
            if len(symbols) >= _MAX_SYMBOLS:
                return symbols
            break
    return symbols


def _module_tokens(path: str) -> set[str]:
    """Identifier-ish tokens a test might reference for a target file.

    For ``src/auth/login.py`` this yields ``{"login", "auth", "src/auth/login"}``
    so a test file mentioning the module name or stem links back to it.
    """
    posix = Path(path).as_posix()
    stem = Path(posix).stem
    tokens = {stem}
    for part in Path(posix).with_suffix("").parts:
        if part and part not in (".", ".."):
            tokens.add(part)
    tokens.add(Path(posix).with_suffix("").as_posix())
    return {token for token in tokens if token}


def _is_test_path(rel: str) -> bool:
    name = Path(rel).name.lower()
    parts = {part.lower() for part in Path(rel).parts}
    return (
        name.startswith("test_")
        or name.startswith("test.")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
        or "test" in parts
        or "tests" in parts
        or "__tests__" in parts
    )


def _iter_project_files(project: Path):
    for root, directories, names in os.walk(project):
        directories[:] = [d for d in directories if d not in IGNORE_DIRS]
        root_path = Path(root)
        for name in names:
            path = root_path / name
            try:
                rel = path.relative_to(project).as_posix()
            except ValueError:
                continue
            yield rel, path


def relevant_tests(project: Path, target_files: list[str]) -> list[str]:
    """Heuristic: test files whose name or content references a target module.

    A test "references" a target if it mentions the target's module stem / a path
    segment, or if their names share a stem (``login.py`` <-> ``test_login.py``).
    Best-effort and bounded; returns a sorted, de-duplicated list of relpaths.
    """
    project = Path(project)
    targets = [t for t in (target_files or []) if t and not _is_test_path(t)]
    if not targets:
        return []
    token_sets = {t: _module_tokens(t) for t in targets}
    target_stems = {Path(t).stem for t in targets}

    found: set[str] = set()
    for rel, path in _iter_project_files(project):
        if not _is_test_path(rel):
            continue
        test_stem = Path(rel).stem
        # Name-based: test_login.py / login.test.js references login.
        stripped = re.sub(r"^test[_.]|[_.]test$|[_.]spec$", "", test_stem.lower())
        if stripped in {s.lower() for s in target_stems}:
            found.add(rel)
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = content.lower()
        for tokens in token_sets.values():
            if any(len(token) >= 3 and token.lower() in lowered for token in tokens):
                found.add(rel)
                break
    return sorted(found)


def recent_commits(project: Path, files: list[str], limit: int = 5) -> list[dict[str, str]]:
    """Recent commits touching `files` via ``git log --oneline -- <files>``.

    Returns ``[{"sha": ..., "subject": ...}]`` newest-first. Best-effort: an empty
    list when the project is not a git repo, git is unavailable, or nothing matched.
    """
    project = Path(project)
    if not (project / ".git").exists():
        return []
    paths = [f for f in (files or []) if f and f.strip()]
    cmd = [
        "git", "-C", str(project), "log",
        f"--max-count={max(1, limit)}",
        "--pretty=format:%h\t%s",
    ]
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    commits: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        commits.append({"sha": sha.strip(), "subject": subject.strip()})
    return commits


# Event kinds that record a project decision, in the event log.
_DECISION_EVENT_KINDS = {"decision", "delegation_decision", "workflow_created"}


def _read_decisions(store: MemoryStore, limit: int = 8) -> list[str]:
    """Prior decisions from DECISIONS.md bullets plus recorded decision events."""
    decisions: list[str] = []
    path = store.project / "DECISIONS.md"
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith(("-", "*")) and len(stripped) > 2:
                    decisions.append(compact_text(stripped.lstrip("-* ").strip(), 240))
                elif stripped.startswith("#") and not stripped.lower().startswith("# decisions"):
                    decisions.append(compact_text(stripped.lstrip("# ").strip(), 240))
        except OSError:
            pass
    for item in store.recent_events(120):
        if item["kind"] in _DECISION_EVENT_KINDS:
            payload = item["payload"]
            text = payload.get("decision") or payload.get("summary") or payload.get("request") or ""
            if text:
                decisions.append(compact_text(str(text), 240))
    # De-duplicate, keep order, cap.
    seen: set[str] = set()
    unique: list[str] = []
    for decision in decisions:
        if decision and decision not in seen:
            seen.add(decision)
            unique.append(decision)
    return unique[:limit]


def _changed_files(store: MemoryStore) -> list[str]:
    """Best-effort uncommitted changes (git porcelain), normalized to relpaths."""
    project = store.project
    if not (project / ".git").exists():
        return []
    try:
        completed = subprocess.run(
            ["git", "-C", str(project), "status", "--porcelain"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    changed: list[str] = []
    for line in completed.stdout.splitlines():
        rel = line[3:].strip() if len(line) > 3 else ""
        if " -> " in rel:  # rename: keep the destination path
            rel = rel.split(" -> ", 1)[1]
        rel = rel.strip().strip('"')
        if rel:
            changed.append(Path(rel).as_posix())
    return sorted(set(changed))


def _blockers(store: MemoryStore, task: dict[str, Any] | None) -> list[str]:
    """Current blockers from task status and the recorded 'Blocker:' in current.md."""
    blockers: list[str] = []
    if task and task.get("status") in {"BLOCKED", "FAILED", "NEEDS_USER"}:
        blockers.append(f"Task {task['task_id']} status is {task['status']}.")
    current = store.state_dir / "current.md"
    if current.exists():
        try:
            for line in current.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("blocker:"):
                    value = stripped.split(":", 1)[1].strip()
                    if value and value.lower() not in {"none", "none recorded.", "none recorded"}:
                        blockers.append(value)
        except OSError:
            pass
    return blockers


def _resolve_target(store: MemoryStore, task_ref: str | None, files: list[str] | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve an intel target into (task or None, target_files).

    Files come from explicit input, else the task's claimed files plus current
    uncommitted changes, so the picture reflects both intended and actual scope.
    """
    task: dict[str, Any] | None = None
    target_files: list[str] = []
    if files:
        target_files.extend(Path(f).as_posix() for f in files if f and f.strip())
    if task_ref:
        task = store.get_task(task_ref)
        if not task:
            raise ValueError(f"Unknown task: {task_ref}")
        target_files.extend(lock["path"] for lock in task.get("locked_files", []))
    if not files:
        target_files.extend(_changed_files(store))
    # De-duplicate, keep order.
    seen: set[str] = set()
    ordered: list[str] = []
    for path in target_files:
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    return task, ordered


def gather_context_intel(
    store: MemoryStore,
    task_ref: str | None = None,
    files: list[str] | None = None,
) -> dict[str, Any]:
    """Aggregate the inspectable context picture for a task or explicit files.

    Combines: top relevant files (claimed + changed), symbols touched, linked
    tests, recent commits touching those files, prior decisions and current
    blockers. Raises ValueError only for a real error (unknown task); a thin
    picture (no files, no tests) is reported in the record, not raised.
    """
    task, target_files = _resolve_target(store, task_ref, files)

    symbols_by_file: dict[str, list[dict[str, str]]] = {}
    for rel in target_files:
        path = store.project / rel
        if path.suffix.lower() in _SOURCE_SUFFIXES and path.exists():
            extracted = extract_symbols(path)
            if extracted:
                symbols_by_file[rel] = extracted

    tests = relevant_tests(store.project, target_files)
    commits = recent_commits(store.project, target_files, limit=5)
    decisions = _read_decisions(store)
    blockers = _blockers(store, task)

    return {
        "task_id": task["task_id"] if task else None,
        "title": task["title"] if task else None,
        "status": task["status"] if task else None,
        "agent": task.get("agent") if task else None,
        "generated_at": utc_now(),
        "files": target_files,
        "symbols": symbols_by_file,
        "tests": tests,
        "recent_commits": commits,
        "decisions": decisions,
        "blockers": blockers,
    }


def render_intel(intel: dict[str, Any]) -> str:
    """Human-readable rendering of a gathered context-intel record."""
    lines: list[str] = []
    label = intel.get("task_id") or "(files)"
    title = f" {intel['title']}" if intel.get("title") else ""
    lines.append(f"# Context Intel: {label}{title}")
    if intel.get("status"):
        lines.append(f"Status: {intel['status']}  Agent: {intel.get('agent') or '-'}")
    lines.append("")

    lines.append("## Relevant Files")
    if intel["files"]:
        for path in intel["files"]:
            lines.append(f"- `{path}`")
    else:
        lines.append("- None (no claims, changes or explicit files).")
    lines.append("")

    lines.append("## Relevant Symbols")
    if intel["symbols"]:
        for path, syms in intel["symbols"].items():
            rendered = ", ".join(f"{s['kind']} {s['name']}" for s in syms)
            lines.append(f"- `{path}`: {rendered}")
    else:
        lines.append("- No symbols extracted.")
    lines.append("")

    lines.append("## Linked Tests")
    if intel["tests"]:
        for path in intel["tests"]:
            lines.append(f"- `{path}`")
    else:
        lines.append("- No linked tests found.")
    lines.append("")

    lines.append("## Recent Changes")
    if intel["recent_commits"]:
        for commit in intel["recent_commits"]:
            lines.append(f"- {commit['sha']} {commit['subject']}")
    else:
        lines.append("- No recent commits (or not a git repo).")
    lines.append("")

    lines.append("## Prior Decisions")
    if intel["decisions"]:
        for decision in intel["decisions"]:
            lines.append(f"- {decision}")
    else:
        lines.append("- No recorded decisions.")
    lines.append("")

    lines.append("## Current Blockers")
    if intel["blockers"]:
        for blocker in intel["blockers"]:
            lines.append(f"- {blocker}")
    else:
        lines.append("- None recorded.")
    lines.append("")
    return "\n".join(lines)


def intel_symbol_set(intel: dict[str, Any]) -> set[str]:
    """Flatten a record's symbols into a comparable ``file::kind name`` set."""
    result: set[str] = set()
    for path, syms in intel.get("symbols", {}).items():
        for sym in syms:
            result.add(f"{path}::{sym['kind']} {sym['name']}")
    return result


def diff_intel(intel_a: dict[str, Any], intel_b: dict[str, Any]) -> dict[str, Any]:
    """Compare two context-intel records and report only-in-A / only-in-B sets.

    Answers "why did one agent know something the other didn't / who got stale
    context" across files, symbols, tests and decisions, plus a token-size delta
    on each record's rendered form.
    """
    files_a, files_b = set(intel_a.get("files", [])), set(intel_b.get("files", []))
    syms_a, syms_b = intel_symbol_set(intel_a), intel_symbol_set(intel_b)
    tests_a, tests_b = set(intel_a.get("tests", [])), set(intel_b.get("tests", []))
    dec_a, dec_b = set(intel_a.get("decisions", [])), set(intel_b.get("decisions", []))

    tokens_a = estimate_tokens(render_intel(intel_a))
    tokens_b = estimate_tokens(render_intel(intel_b))

    return {
        "ref_a": intel_a.get("task_id") or "(files A)",
        "ref_b": intel_b.get("task_id") or "(files B)",
        "files_only_a": sorted(files_a - files_b),
        "files_only_b": sorted(files_b - files_a),
        "files_shared": sorted(files_a & files_b),
        "symbols_only_a": sorted(syms_a - syms_b),
        "symbols_only_b": sorted(syms_b - syms_a),
        "tests_only_a": sorted(tests_a - tests_b),
        "tests_only_b": sorted(tests_b - tests_a),
        "decisions_only_a": sorted(dec_a - dec_b),
        "decisions_only_b": sorted(dec_b - dec_a),
        "tokens_a": tokens_a,
        "tokens_b": tokens_b,
        "token_delta": tokens_a - tokens_b,
    }


def render_diff(diff: dict[str, Any]) -> str:
    """Human-readable rendering of a context-intel diff."""
    lines: list[str] = [f"# Context Diff: {diff['ref_a']} vs {diff['ref_b']}", ""]

    def block(title: str, only_a: list[str], only_b: list[str]) -> None:
        lines.append(f"## {title}")
        lines.append(f"Only in {diff['ref_a']}:")
        lines.extend(f"  - {item}" for item in only_a) if only_a else lines.append("  - (none)")
        lines.append(f"Only in {diff['ref_b']}:")
        lines.extend(f"  - {item}" for item in only_b) if only_b else lines.append("  - (none)")
        lines.append("")

    block("Files", diff["files_only_a"], diff["files_only_b"])
    block("Symbols", diff["symbols_only_a"], diff["symbols_only_b"])
    block("Tests", diff["tests_only_a"], diff["tests_only_b"])
    block("Decisions", diff["decisions_only_a"], diff["decisions_only_b"])

    lines.append("## Token Size")
    lines.append(f"- {diff['ref_a']}: {diff['tokens_a']} tokens")
    lines.append(f"- {diff['ref_b']}: {diff['tokens_b']} tokens")
    lines.append(f"- Delta (A - B): {diff['token_delta']} tokens")
    lines.append("")
    return "\n".join(lines)


def _parse_iso(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _staleness_hours(store: MemoryStore, intel: dict[str, Any]) -> float | None:
    """Max age in hours across included memory/handoff/decision timestamps.

    Considers the freshest-source picture: the latest handoff/decision-style event
    and the current.md mtime. Returns the *oldest* (max age) so a stale source is
    not hidden by a fresh one. None when no timestamp is available.
    """
    now = dt.datetime.now(dt.timezone.utc)
    timestamps: list[dt.datetime] = []
    for item in store.recent_events(200):
        if item["kind"] in (_DECISION_EVENT_KINDS | {"handoff"}):
            parsed = _parse_iso(item.get("created_at", ""))
            if parsed:
                timestamps.append(parsed)
    current = store.state_dir / "current.md"
    if current.exists():
        timestamps.append(dt.datetime.fromtimestamp(current.stat().st_mtime, tz=dt.timezone.utc))
    if not timestamps:
        return None
    oldest = min(timestamps)
    return round((now - oldest).total_seconds() / 3600.0, 2)


# A packet above this estimated-token size counts toward the risk heuristic. Tied
# to the repo's "deep" context budget so scoring tracks how Continuum budgets.
CONTEXT_LARGE_TOKENS = 6_000


def score_intel(store: MemoryStore, task_ref: str | None = None, files: list[str] | None = None) -> dict[str, Any]:
    """Score the context picture for a task: tokens, sources, staleness, risk.

    Risk is a deterministic low/med/high heuristic from out-of-scope changes,
    missing tests, stale context and packet size. `missing_info` is a checklist
    of expected sections that came back empty. Raises ValueError only on a real
    error (unknown task).
    """
    intel = gather_context_intel(store, task_ref, files)
    rendered = render_intel(intel)
    estimated_tokens = estimate_tokens(rendered)

    # Distinct sources that fed the picture.
    sources: list[str] = []
    if intel["files"]:
        sources.append("files")
    if intel["symbols"]:
        sources.append("symbols")
    if intel["tests"]:
        sources.append("tests")
    if intel["recent_commits"]:
        sources.append("recent_commits")
    if intel["decisions"]:
        sources.append("decisions")
    if intel["blockers"]:
        sources.append("blockers")

    staleness_hours = _staleness_hours(store, intel)
    staleness_days = round(staleness_hours / 24.0, 2) if staleness_hours is not None else None

    # Missing-info checklist: expected sections that are empty.
    missing_info: list[str] = []
    if not intel["files"]:
        missing_info.append("no owned or changed files")
    if not intel["symbols"]:
        missing_info.append("no symbols extracted")
    if not intel["tests"]:
        missing_info.append("no tests linked")
    if not intel["recent_commits"]:
        missing_info.append("no recent commits")
    if not intel["decisions"]:
        missing_info.append("no recent decisions")

    # Out-of-scope: changed files not among the task's claimed files.
    out_of_scope: list[str] = []
    if task_ref:
        task = store.get_task(task_ref)
        if task:
            claimed = {lock["path"] for lock in task.get("locked_files", [])}
            if claimed:
                out_of_scope = sorted(set(_changed_files(store)) - claimed)

    # Deterministic risk scoring.
    risk_points = 0
    risk_reasons: list[str] = []
    if out_of_scope:
        risk_points += 2
        risk_reasons.append(f"{len(out_of_scope)} out-of-scope changed file(s)")
    if not intel["tests"]:
        risk_points += 1
        risk_reasons.append("no linked tests")
    if staleness_days is not None and staleness_days >= 7:
        risk_points += 2
        risk_reasons.append(f"stale context ({staleness_days} days)")
    elif staleness_hours is not None and staleness_hours >= 48:
        risk_points += 1
        risk_reasons.append(f"aging context ({staleness_hours} h)")
    if estimated_tokens >= CONTEXT_LARGE_TOKENS:
        risk_points += 1
        risk_reasons.append(f"large packet ({estimated_tokens} tokens)")
    if not intel["files"]:
        risk_points += 1
        risk_reasons.append("no files in scope")

    if risk_points >= 4:
        risk_level = "high"
    elif risk_points >= 2:
        risk_level = "med"
    else:
        risk_level = "low"

    return {
        "task_id": intel["task_id"],
        "estimated_tokens": estimated_tokens,
        "source_count": len(sources),
        "sources": sources,
        "staleness_hours": staleness_hours,
        "staleness_days": staleness_days,
        "out_of_scope_files": out_of_scope,
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
        "missing_info": missing_info,
    }


def render_score(score: dict[str, Any]) -> str:
    """Human-readable rendering of a context score."""
    lines: list[str] = [f"# Context Score: {score['task_id'] or '(files)'}", ""]
    lines.append(f"Estimated tokens: {score['estimated_tokens']}")
    lines.append(f"Distinct sources: {score['source_count']} ({', '.join(score['sources']) or 'none'})")
    if score["staleness_hours"] is None:
        lines.append("Staleness: unknown (no dated sources)")
    else:
        lines.append(f"Staleness: {score['staleness_hours']} h ({score['staleness_days']} days)")
    lines.append(f"Risk level: {score['risk_level']}")
    if score["risk_reasons"]:
        lines.append("Risk reasons:")
        lines.extend(f"  - {reason}" for reason in score["risk_reasons"])
    if score["out_of_scope_files"]:
        lines.append("Out-of-scope changed files:")
        lines.extend(f"  - `{path}`" for path in score["out_of_scope_files"])
    lines.append("Missing info:")
    if score["missing_info"]:
        lines.extend(f"  - {item}" for item in score["missing_info"])
    else:
        lines.append("  - none (all expected sections populated)")
    lines.append("")
    return "\n".join(lines)
