"""Enterprise governance policy for Continuum (`.continuum/policy.json`).

The policy file lets an organization constrain what Continuum may do and what
may leave the local machine. Continuum verifies against this policy
deterministically; it does not ask a model to honor it. All keys are optional
and have sensible defaults when absent.

Schema (all top-level keys optional):

    allowed_providers          list[str]  Providers permitted to claim files or
                                          run operations. Empty/absent = every
                                          configured provider is allowed.
    denied_files               list[str]  Glob patterns for paths that may never
                                          be claimed or edited.
    sensitive_globs            list[str]  Glob patterns for files whose CONTENT
                                          must never appear in egress context
                                          (e.g. ".env", "**/*.pem", "secrets/**").
    required_tests_before_merge bool      Whether a green test gate is required
                                          before a worktree may merge. Default
                                          true.
    max_context_tokens         int|null   Optional hard cap on egress context
                                          token estimate. Absent = no extra cap.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICY_FILENAME = "policy.json"

ALLOWED_KEYS = {
    "allowed_providers",
    "denied_files",
    "sensitive_globs",
    "required_tests_before_merge",
    "max_context_tokens",
    "_comment",  # Reserved for human comments-as-strings in the starter file.
}


class PolicyError(ValueError):
    """Raised when a policy file is malformed or violates the schema."""


@dataclass(frozen=True)
class Policy:
    allowed_providers: list[str] = field(default_factory=list)
    denied_files: list[str] = field(default_factory=list)
    sensitive_globs: list[str] = field(default_factory=list)
    required_tests_before_merge: bool = True
    max_context_tokens: int | None = None
    source: str | None = None  # Path the policy was loaded from, or None for defaults.

    def provider_allowed(self, provider: str) -> bool:
        if not self.allowed_providers:
            return True
        return provider in self.allowed_providers

    def is_denied_file(self, path: str) -> bool:
        return _matches_any(path, self.denied_files)

    def is_sensitive_file(self, path: str) -> bool:
        return _matches_any(path, self.sensitive_globs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_providers": list(self.allowed_providers),
            "denied_files": list(self.denied_files),
            "sensitive_globs": list(self.sensitive_globs),
            "required_tests_before_merge": self.required_tests_before_merge,
            "max_context_tokens": self.max_context_tokens,
        }


def _normalize_glob(value: str) -> str:
    normalized = str(Path(value).as_posix())
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _matches_any(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    candidate = _normalize_glob(path)
    name = candidate.rsplit("/", 1)[-1]
    for pattern in patterns:
        normalized = _normalize_glob(pattern)
        if fnmatch.fnmatch(candidate, normalized) or fnmatch.fnmatch(name, normalized):
            continue_match = True
        else:
            # `**/` prefixed patterns should also match top-level paths.
            continue_match = normalized.startswith("**/") and fnmatch.fnmatch(candidate, normalized[3:])
        if continue_match:
            return True
    return False


def _validate_str_list(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PolicyError(f"Policy key `{key}` must be a list of strings.")
    return [item for item in value if item.strip()]


def parse_policy(data: dict[str, Any], source: str | None = None) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("Policy file must contain a JSON object.")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise PolicyError(
            "Unknown policy key(s): " + ", ".join(sorted(unknown)) + ". "
            "Allowed keys: " + ", ".join(sorted(ALLOWED_KEYS - {"_comment"})) + "."
        )
    allowed_providers = _validate_str_list(data.get("allowed_providers", []), "allowed_providers")
    denied_files = _validate_str_list(data.get("denied_files", []), "denied_files")
    sensitive_globs = _validate_str_list(data.get("sensitive_globs", []), "sensitive_globs")
    required = data.get("required_tests_before_merge", True)
    if not isinstance(required, bool):
        raise PolicyError("Policy key `required_tests_before_merge` must be a boolean.")
    max_tokens = data.get("max_context_tokens", None)
    if max_tokens is not None:
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise PolicyError("Policy key `max_context_tokens` must be a positive integer.")
    return Policy(
        allowed_providers=allowed_providers,
        denied_files=denied_files,
        sensitive_globs=sensitive_globs,
        required_tests_before_merge=required,
        max_context_tokens=max_tokens,
        source=source,
    )


def policy_path(state_dir: Path) -> Path:
    return state_dir / POLICY_FILENAME


def load_policy(state_dir: Path) -> Policy:
    """Load `.continuum/policy.json` with defaults. Raises PolicyError if the
    file exists but is malformed or violates the schema."""
    path = policy_path(state_dir)
    if not path.exists():
        return Policy(source=None)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PolicyError(f"Policy file is not valid JSON: {error}") from error
    except OSError as error:
        raise PolicyError(f"Could not read policy file: {error}") from error
    return parse_policy(data, source=str(path))


STARTER_POLICY: dict[str, Any] = {
    "_comment": (
        "Continuum governance policy. All keys are optional. "
        "allowed_providers: providers permitted to claim/edit (empty = all). "
        "denied_files: globs that may never be claimed/edited. "
        "sensitive_globs: files whose content is excluded from egress context. "
        "required_tests_before_merge: gate merges on a green test result. "
        "max_context_tokens: optional cap on egress context size."
    ),
    "allowed_providers": [],
    "denied_files": [".env", "**/*.pem", "secrets/**"],
    "sensitive_globs": [".env", "**/*.pem", "**/*.key", "secrets/**"],
    "required_tests_before_merge": True,
    "max_context_tokens": None,
}


def write_starter_policy(state_dir: Path, force: bool = False) -> Path:
    """Write a documented starter policy. Refuses to overwrite unless `force`."""
    path = policy_path(state_dir)
    if path.exists() and not force:
        raise PolicyError(
            f"Policy already exists: {path}. Re-run with --force to overwrite."
        )
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(STARTER_POLICY, indent=2) + "\n", encoding="utf-8")
    return path
