"""Notice when an agent runs out, and remember it across sessions.

Continuum cannot ask a provider how much quota is left. There is no API for it,
and the CLIs say nothing until they refuse. So nothing here reports a percentage
or a remaining balance, because either would be invented.

What is knowable splits into two kinds, and the difference is kept visible
everywhere it is shown:

- **Observed.** The agent said so, in its own output, quoted verbatim. A limit
  message, and sometimes a reset time it stated itself. This is the only ground
  truth available.
- **Measured.** Continuum's own rough count of the text it sent and saw, at
  roughly four characters per token. It excludes everything the agent did
  internally, so it is a floor rather than a total.

Anything else is unknown and is reported as unknown.

The false-positive problem is the interesting one. An agent working in this very
repository will print "usage limit reached" while reading this file, so matching
the phrase cannot mean the agent is blocked. Evidence is therefore separated
from conclusion: a signal is only confirmed when the agent also stated a reset
time, or repeated itself, or the session then failed. Unconfirmed signals are
recorded and visible, but never influence which agent runs next.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .adapters import ANSI_ESCAPE

if TYPE_CHECKING:
    from .core import MemoryStore

# Phrases agents use when they have actually run out. Bare "429" is excluded
# deliberately: it appears in ordinary output far too often.
EXHAUSTED = (
    "usage limit reached",
    "reached your usage limit",
    "rate limit exceeded",
    "quota exceeded",
    "resource_exhausted",
    "insufficient_quota",
    "out of credits",
    "insufficient credits",
    "credit balance is too low",
    "429 too many requests",
    "http 429",
    "error 429",
)
THROTTLED = ("rate limited", "please slow down", "retry after", "too many requests")

RELATIVE_RESET = re.compile(
    r"(?:try again|retry|resets?|available again)\s+in\s+(\d+)\s*(second|minute|hour|day)s?",
    re.IGNORECASE,
)
CLOCK_RESET = re.compile(
    r"resets?\s+(?:at|on)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE
)
UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True)
class LimitSignal:
    kind: str          # "exhausted" or "throttled"
    evidence: str      # the agent's own line, cleaned but not reworded
    reset_at: str | None = None


def clean(text: str) -> str:
    return " ".join(ANSI_ESCAPE.sub("", text).replace("\r", "\n").split())


def parse_reset(text: str, now: dt.datetime | None = None) -> str | None:
    """Read a reset time out of the agent's own words, or return None.

    Only forms the agent actually stated are accepted. A reset time is never
    extrapolated from how long ago a limit was hit, because that would be a
    guess presented in the same place as a fact.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    match = RELATIVE_RESET.search(text)
    if match:
        seconds = int(match.group(1)) * UNITS[match.group(2).lower()]
        return (now + dt.timedelta(seconds=seconds)).replace(microsecond=0).isoformat()
    match = CLOCK_RESET.search(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        local = now.astimezone()
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= local:
            target += dt.timedelta(days=1)
        return target.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    return None


def scan(chunk: str) -> list[LimitSignal]:
    """Find limit messages in a piece of agent output."""
    signals = []
    for line in ANSI_ESCAPE.sub("", chunk).replace("\r", "\n").splitlines():
        stripped = clean(line)
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(phrase in lowered for phrase in EXHAUSTED):
            signals.append(LimitSignal("exhausted", stripped[:200], parse_reset(stripped)))
        elif any(phrase in lowered for phrase in THROTTLED):
            signals.append(LimitSignal("throttled", stripped[:200], parse_reset(stripped)))
    return signals


class SessionTracker:
    """Collects limit signals during one session and decides what to believe.

    A phrase appearing once in output that the agent was merely reading is not
    evidence that the agent is blocked. Confirmation requires the agent to have
    stated a reset time, repeated itself, or ended the session badly.
    """

    REPEATS_FOR_CONFIRMATION = 3

    def __init__(self, agent: str, session: str) -> None:
        self.agent = agent
        self.session = session
        self.signals: list[LimitSignal] = []
        self.counts: dict[str, int] = {}
        self._seen: set[str] = set()

    def observe(self, chunk: str) -> list[LimitSignal]:
        """Record any new signals in a chunk. Returns the ones just confirmed."""
        confirmed = []
        for signal in scan(chunk):
            key = f"{signal.kind}:{signal.evidence}"
            self.counts[signal.kind] = self.counts.get(signal.kind, 0) + 1
            if key in self._seen:
                continue
            self._seen.add(key)
            self.signals.append(signal)
            if signal.reset_at:
                confirmed.append(signal)
        return confirmed

    def confirmed(self, returncode: int | None = None) -> list[LimitSignal]:
        """The signals worth acting on, once the session's outcome is known."""
        if not self.signals:
            return []
        stated_reset = [signal for signal in self.signals if signal.reset_at]
        if stated_reset:
            return stated_reset
        repeated = self.counts.get("exhausted", 0) >= self.REPEATS_FOR_CONFIRMATION
        failed = bool(returncode)
        if repeated or failed:
            return [signal for signal in self.signals if signal.kind == "exhausted"]
        return []


def record_session(
    store: "MemoryStore",
    *,
    agent: str,
    session: str,
    started_at: str,
    ended_at: str,
    injected_tokens: int,
    output_tokens: int,
    estimate_quality: str,
    checkpoint_triggered: bool,
    returncode: int | None,
    tracker: "SessionTracker | None" = None,
) -> None:
    """Persist what one session consumed, and anything it said about limits.

    Best-effort: a read-only store, which is what an agent sandboxing its MCP
    server gives Continuum, must not turn recording usage into a failed session.
    """
    try:
        store.record_agent_usage(
            session=session,
            agent=agent,
            started_at=started_at,
            ended_at=ended_at,
            injected_tokens=injected_tokens,
            output_tokens=output_tokens,
            estimate_quality=estimate_quality,
            checkpoint_triggered=checkpoint_triggered,
            returncode=returncode,
        )
        if tracker:
            confirmed = {id(signal) for signal in tracker.confirmed(returncode)}
            for signal in tracker.signals:
                is_confirmed = id(signal) in confirmed
                store.record_limit_signal(
                    agent=agent,
                    kind=signal.kind,
                    evidence=signal.evidence,
                    session=session,
                    reset_at=signal.reset_at,
                    confirmed=is_confirmed,
                )
                if is_confirmed:
                    store.event(
                        "quota_signal",
                        {
                            "agent": agent,
                            "kind": signal.kind,
                            "evidence": signal.evidence,
                            "reset_at": signal.reset_at,
                            "summary": f"{agent} reported a provider limit",
                        },
                    )
    except (sqlite3.Error, OSError, ValueError):
        return


@dataclass(frozen=True)
class Headroom:
    agent: str
    state: str                  # "blocked", "recently_limited" or "unknown"
    reason: str
    reset_at: str | None = None
    evidence: str | None = None
    sessions: int = 0
    estimated_tokens: int = 0
    unconfirmed: int = 0


def _minutes_since(timestamp: str, now: dt.datetime) -> float | None:
    try:
        moment = dt.datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return (now - moment).total_seconds() / 60


def _ago(timestamp: str, now: dt.datetime) -> str:
    try:
        moment = dt.datetime.fromisoformat(timestamp)
    except ValueError:
        return "recently"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    minutes = max(0, int((now - moment).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 1440:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


def headroom(store: "MemoryStore", agent: str, window_hours: int = 5,
             cooldown_minutes: int = 60, now: dt.datetime | None = None) -> Headroom:
    """What is known about one agent's standing, and nothing more."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=window_hours)).replace(microsecond=0).isoformat()
    try:
        usage = store.agent_usage_since(since, agent)
        signals = store.limit_signals_since(since, agent)
    except (sqlite3.Error, OSError):
        usage, signals = [], []

    sessions = len(usage)
    tokens = sum(int(row.get("estimated_tokens") or 0) for row in usage)
    unconfirmed = [row for row in signals if not row.get("confirmed")]
    signals = [row for row in signals if row.get("confirmed")]
    if not signals:
        # An unconfirmed mention is worth showing and not worth acting on: an
        # agent reading this file prints the phrase.
        note = (f"{len(unconfirmed)} unconfirmed mention(s), not acted on"
                if unconfirmed else "no limit reported")
        return Headroom(agent, "unknown", note, sessions=sessions, estimated_tokens=tokens,
                        evidence=unconfirmed[0]["evidence"] if unconfirmed else None,
                        unconfirmed=len(unconfirmed))

    latest = signals[0]
    reset_at = latest.get("reset_at")
    evidence = latest.get("evidence")
    observed = latest.get("observed_at") or since
    when = _ago(str(observed), now)

    if reset_at:
        try:
            moment = dt.datetime.fromisoformat(str(reset_at))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            moment = None
        if moment is not None:
            if moment > now:
                return Headroom(
                    agent, "blocked",
                    f"reported a limit {when} and said it resets at "
                    f"{moment.astimezone().strftime('%H:%M')}",
                    reset_at=str(reset_at), evidence=evidence,
                    sessions=sessions, estimated_tokens=tokens,
                )
            # The agent told us when it would be back and that time has passed.
            # Its own word beats a cooldown Continuum invented.
            return Headroom(
                agent, "recently_limited",
                f"reported a limit {when}, and the reset time it gave has passed",
                evidence=evidence, sessions=sessions, estimated_tokens=tokens,
            )

    minutes_since = _minutes_since(str(observed), now)
    if minutes_since is not None and minutes_since < cooldown_minutes:
        return Headroom(agent, "blocked",
                        f"reported a limit {when} and gave no reset time",
                        evidence=evidence, sessions=sessions, estimated_tokens=tokens)
    return Headroom(agent, "recently_limited", f"reported a limit {when}",
                    evidence=evidence, sessions=sessions, estimated_tokens=tokens)


def rank(store: "MemoryStore", agents: list[str], now: dt.datetime | None = None) -> list[Headroom]:
    """Agents in preference order: unknown standing first, blocked last."""
    order = {"unknown": 0, "recently_limited": 1, "blocked": 2}
    found = [headroom(store, agent, now=now) for agent in agents]
    return sorted(found, key=lambda item: (order.get(item.state, 0), item.agent))


def render(entries: list[Headroom]) -> str:
    """The `continuum limits` view. Says which tier every figure came from."""
    if not entries:
        return "\n".join([
            "No agent CLIs installed, so there is nothing to report.",
            "",
            "What these mean:",
            "  observed  text Continuum read in the agent's own output",
            "  estimated Continuum's own rough count of what it sent and saw",
            "  unknown   how much quota you actually have left. There is no way to ask.",
        ])
    lines = []
    for entry in entries:
        lines.append(entry.agent)
        if entry.evidence:
            lines.append(f'  it said: "{entry.evidence}"')
            lines.append(f"  state: {entry.state.replace('_', ' ')}, {entry.reason}")
            if entry.unconfirmed:
                lines.append(
                    "  that phrase can appear in output an agent was merely reading, "
                    "so it was recorded but not acted on"
                )
        else:
            lines.append("  no limit message recorded")
        lines.append(
            f"  this window: {entry.sessions} session(s), "
            f"~{entry.estimated_tokens:,} estimated tokens"
        )
        lines.append("")
    lines += [
        "What these mean:",
        "  observed  text Continuum read in the agent's own output, quoted above",
        "  estimated Continuum's own rough count, about four characters per token, of",
        "            what it sent and saw. It cannot see the agent's own file reads or",
        "            tool calls, so this is a floor rather than a total.",
        "  unknown   how much quota you actually have left. There is no way to ask.",
    ]
    return "\n".join(lines)
