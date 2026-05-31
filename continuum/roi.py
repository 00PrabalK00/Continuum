"""Cost-aware routing and Agent ROI evidence."""

from __future__ import annotations

from typing import Any

from .core import MemoryStore
from .evidence import gather_evidence
from .flight import gather_flight_record


def roi_summary(store: MemoryStore) -> dict[str, Any]:
    tasks = store.list_tasks(limit=500) if store.db_file.exists() else []
    completed = [task for task in tasks if task["status"] == "DONE"]
    failed = [task for task in tasks if task["status"] == "FAILED"]
    flights = []
    for task in tasks:
        try:
            flights.append(gather_flight_record(store, task["task_id"]))
        except Exception:
            continue

    tokens = sum(int((record.get("cost_estimate") or {}).get("estimated_tokens") or 0) for record in flights)
    changed_files = sum(len(record.get("files_touched", [])) for record in flights)
    out_of_scope = 0
    tests_passed = 0
    review_rejections = 0
    merge_ready = 0
    for record in flights:
        out_of_scope += sum(1 for risk in record.get("risks", []) if "Out-of-scope edit" in risk)
        if (record.get("test_evidence") or {}).get("result") == "PASS":
            tests_passed += 1
        if (record.get("review_notes") or {}).get("result") == "CHANGES_REQUESTED":
            review_rejections += 1
        if record.get("final_status") in {"merge_ready", "merged"}:
            merge_ready += 1

    events = store.recent_events(1000) if store.db_file.exists() else []
    reruns = sum(1 for event in events if event["kind"] in {"workflow_retry", "workflow_status"} and "retry" in str(event["payload"]).lower())
    manual_corrections = sum(1 for event in events if event["kind"] in {"claim_released", "claim_recovered", "worktree_review"} and "change" in str(event["payload"]).lower())

    provider_usage: dict[str, dict[str, Any]] = {}
    for event in events:
        provider = event["payload"].get("provider") or event["payload"].get("agent")
        model = event["payload"].get("model")
        if not provider:
            continue
        bucket = provider_usage.setdefault(str(provider), {"provider": str(provider), "models": set(), "events": 0})
        if model:
            bucket["models"].add(str(model))
        bucket["events"] += 1
    providers = [
        {"provider": item["provider"], "models": sorted(item["models"]), "events": item["events"]}
        for item in sorted(provider_usage.values(), key=lambda value: value["provider"])
    ]

    return {
        "tasks_completed": len(completed),
        "tasks_failed": len(failed),
        "tasks_total": len(tasks),
        "flight_records": len(flights),
        "estimated_tokens": tokens,
        "estimated_cost_usd": None,
        "changed_files": changed_files,
        "cost_per_accepted_change_tokens": round(tokens / max(merge_ready, 1), 2) if flights else 0,
        "files_changed_outside_scope": out_of_scope,
        "tests_passed": tests_passed,
        "review_rejections": review_rejections,
        "reruns": reruns,
        "manual_corrections": manual_corrections,
        "merge_ready_tasks": merge_ready,
        "provider_usage": providers,
        "recommendations": routing_recommendations(),
    }


def routing_recommendations() -> list[dict[str, str]]:
    return [
        {"task": "memory compression", "provider": "ollama", "reason": "local, cheap and private for summaries"},
        {"task": "broad exploration", "provider": "gemini_cli", "reason": "useful for wide repository scans and edge cases"},
        {"task": "complex implementation", "provider": "claude_code", "reason": "strong default for code edits under file claims"},
        {"task": "tests and patches", "provider": "codex", "reason": "good fit for isolated test repair and patch loops"},
        {"task": "planning fallback", "provider": "openrouter", "reason": "hosted reasoning when local planning is insufficient"},
    ]


def render_roi(summary: dict[str, Any]) -> str:
    lines = [
        "# Agent ROI Evidence",
        "",
        f"- Tasks completed: {summary['tasks_completed']}",
        f"- Tasks failed: {summary['tasks_failed']}",
        f"- Flight records: {summary['flight_records']}",
        f"- Estimated tokens: {summary['estimated_tokens']}",
        f"- Cost per accepted change: {summary['cost_per_accepted_change_tokens']} tokens",
        f"- Files changed outside scope: {summary['files_changed_outside_scope']}",
        f"- Tests passed: {summary['tests_passed']}",
        f"- Review rejections: {summary['review_rejections']}",
        f"- Reruns: {summary['reruns']}",
        f"- Manual corrections: {summary['manual_corrections']}",
        "",
        "## Provider Usage",
    ]
    if summary["provider_usage"]:
        for item in summary["provider_usage"]:
            models = ", ".join(item["models"]) or "default"
            lines.append(f"- {item['provider']}: {item['events']} event(s), models: {models}")
    else:
        lines.append("- No provider usage recorded.")
    lines.extend(["", "## Routing Recommendations"])
    for item in summary["recommendations"]:
        lines.append(f"- {item['task']}: {item['provider']} ({item['reason']})")
    lines.append("")
    return "\n".join(lines)


def task_metrics(store: MemoryStore, task_ref: str) -> dict[str, Any]:
    evidence = gather_evidence(store, task_ref)
    flight = gather_flight_record(store, task_ref)
    return {
        "task_id": evidence["task_id"],
        "label": evidence["title"],
        "tokens_used": (flight.get("cost_estimate") or {}).get("estimated_tokens") or 0,
        "failed_attempts": sum(1 for event in evidence["events"] if event["kind"] == "task_status" and event["detail"] == "FAILED"),
        "files_touched_outside_scope": sum(1 for risk in evidence["risks"] if "Out-of-scope edit" in risk),
        "tests_run": 1 if evidence["test_gate"].get("result") else 0,
        "merge_ready": flight.get("final_status") in {"merge_ready", "merged"},
        "context_resets": sum(1 for event in evidence["events"] if event["kind"] == "context_checkpoint"),
        "human_corrections": sum(1 for risk in evidence["risks"] if "review" in risk.lower() or "gate" in risk.lower()),
        "changed_files": len(evidence["changed_files"]),
        "review_recorded": bool(evidence["review_gate"].get("result")),
    }
