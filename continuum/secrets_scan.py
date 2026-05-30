"""Conservative secret detection and redaction for pre-egress context.

Continuum is the deterministic source of truth: it scrubs locally identifiable
secrets out of any text before that text can leave the machine (sent to a hosted
provider such as OpenRouter or written into a context packet read by an external
agent). The regexes here deliberately prefer false-negatives over corrupting
ordinary source code: a missed exotic token is recoverable, a mangled code file
is not. The scanner never logs or returns the raw secret value, only a masked
preview suitable for human inspection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REDACTION_TEMPLATE = "[REDACTED:{kind}]"


@dataclass(frozen=True)
class Finding:
    """One detected secret. `match` is kept only in-process for redaction; use
    `preview` (masked) for any output, audit event or user-facing message."""

    kind: str
    match: str
    start: int
    end: int

    @property
    def preview(self) -> str:
        return mask_secret(self.match)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "preview": self.preview, "start": self.start, "end": self.end}


def mask_secret(value: str) -> str:
    """Return a masked preview that never reveals the full secret."""
    value = value.strip()
    if len(value) <= 8:
        return value[0] + "***" if value else "***"
    return f"{value[:4]}...{value[-2:]} ({len(value)} chars)"


# Order matters: more specific / higher-confidence patterns first so that a token
# matched by a precise rule is not also reported by a broad assignment rule.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|live-|or-v1-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "bearer_token",
        re.compile(r"\b[Bb]earer\s+[A-Za-z0-9_\-.=]{16,}"),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    # Generic `KEY = "value"` / `KEY=value` assignments where the key name strongly
    # implies a secret. Quotes optional; value must be reasonably long to avoid
    # matching placeholders like `api_key=` or `secret=x`.
    (
        "assigned_secret",
        re.compile(
            r"""(?ix)
            \b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token
                |client[_-]?secret|password|passwd|private[_-]?key|token)\b
            \s*[:=]\s*
            (?P<quote>["']?)
            (?P<value>[A-Za-z0-9_\-./+=]{12,})
            (?P=quote)
            """
        ),
    ),
)


def scan_text(text: str) -> list[Finding]:
    """Return every detected secret in `text`, ordered by position.

    Overlapping matches are de-duplicated: a span already covered by an
    earlier (higher-confidence) finding is skipped, so a token caught by a
    precise rule is not double-reported by the broad assignment rule.
    """
    if not text:
        return []
    findings: list[Finding] = []
    claimed: list[tuple[int, int]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            # For assignment-style secrets, redact only the value group so the
            # key name stays readable in the redacted output.
            if "value" in match.groupdict() and match.group("value") is not None:
                start, end = match.span("value")
            else:
                start, end = match.span()
            if any(start < c_end and end > c_start for c_start, c_end in claimed):
                continue
            claimed.append((start, end))
            findings.append(Finding(kind=kind, match=text[start:end], start=start, end=end))
    findings.sort(key=lambda item: item.start)
    return findings


def redact_text(text: str) -> tuple[str, list[Finding]]:
    """Replace every detected secret with `[REDACTED:<kind>]`.

    Returns the cleaned text and the findings. Replacement is done from the end
    of the string backwards so earlier offsets stay valid.
    """
    findings = scan_text(text)
    if not findings:
        return text, []
    cleaned = text
    for finding in sorted(findings, key=lambda item: item.start, reverse=True):
        marker = REDACTION_TEMPLATE.format(kind=finding.kind)
        cleaned = cleaned[: finding.start] + marker + cleaned[finding.end :]
    return cleaned, findings


def summarize_findings(findings: list[Finding]) -> dict[str, int]:
    """Count findings by kind for audit events (never includes secret values)."""
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return counts
