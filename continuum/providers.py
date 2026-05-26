"""Model provider backends for local memory intelligence and hosted reasoning."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .core import compact_text, write_text

DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "enabled": False,
        "kind": "model",
        "type": "local",
        "base_url": "http://localhost:11434/v1",
        "chat_model": "llama3.1:8b",
        "embedding_model": "nomic-embed-text",
        "use_for": ["summarize", "embed", "dedupe", "handoff"],
    },
    "openrouter": {
        "enabled": False,
        "kind": "model",
        "type": "hosted",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
        "use_for": ["plan", "review", "fallback"],
    },
    "claude_code": {"enabled": False, "kind": "agent", "type": "cli", "command": "claude", "prompt_args": ["-p"]},
    "gemini_cli": {"enabled": False, "kind": "agent", "type": "cli", "command": "gemini", "prompt_args": ["-p"]},
    "codex": {"enabled": False, "kind": "agent", "type": "cli", "command": "codex", "prompt_args": ["exec"]},
}

DEFAULT_ROUTING = {
    "summarize": {"primary": "ollama", "fallback": "openrouter"},
    "embed": {"primary": "ollama", "fallback": None},
    "handoff": {"primary": "ollama", "fallback": "openrouter"},
    "plan": {"primary": "openrouter", "fallback": "claude_code"},
    "code": {"primary": "claude_code", "fallback": "codex"},
    "test_fix": {"primary": "codex", "fallback": "claude_code"},
    "broad_review": {"primary": "gemini_cli", "fallback": "openrouter"},
}


class ProviderError(RuntimeError):
    pass


class ProviderManager:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.config_file = state_dir / "providers.json"

    def ensure_config(self) -> None:
        if not self.config_file.exists():
            write_text(
                self.config_file,
                json.dumps({"providers": DEFAULT_PROVIDERS, "routing": DEFAULT_ROUTING}, indent=2) + "\n",
            )

    def read(self) -> dict[str, Any]:
        self.ensure_config()
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def add(self, name: str) -> dict[str, Any]:
        if name not in DEFAULT_PROVIDERS:
            raise ProviderError(f"Unknown built-in provider: {name}")
        config = self.read()
        value = dict(DEFAULT_PROVIDERS[name])
        value["enabled"] = True
        config["providers"][name] = value
        write_text(self.config_file, json.dumps(config, indent=2) + "\n")
        return value

    def provider(self, name: str) -> dict[str, Any]:
        value = self.read()["providers"].get(name)
        if not value:
            raise ProviderError(f"Provider is not configured: {name}")
        return value

    @staticmethod
    def _json_request(url: str, payload: dict[str, Any] | None, headers: dict[str, str], timeout: int = 15) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError(f"Provider request failed: {error}") from error

    def _headers(self, name: str, provider: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if name == "ollama":
            headers["Authorization"] = "Bearer ollama"
        elif name == "openrouter":
            variable = provider.get("api_key_env", "OPENROUTER_API_KEY")
            key = os.environ.get(variable)
            if not key:
                raise ProviderError(f"Missing environment variable: {variable}")
            headers["Authorization"] = f"Bearer {key}"
            headers["HTTP-Referer"] = "https://github.com/continuum"
            headers["X-Title"] = "Continuum"
        return headers

    def test(self, name: str) -> str:
        provider = self.provider(name)
        if provider.get("kind") == "agent":
            import shutil

            command = provider.get("command", name)
            path = shutil.which(command)
            return f"available: {path}" if path else f"not found on PATH: {command}"
        headers = self._headers(name, provider)
        response = self._json_request(provider["base_url"].rstrip("/") + "/models", None, headers, timeout=5)
        models = response.get("data", [])
        return f"connected: {len(models)} model(s) visible"

    def models(self, name: str) -> list[str]:
        provider = self.provider(name)
        response = self._json_request(provider["base_url"].rstrip("/") + "/models", None, self._headers(name, provider), timeout=5)
        return [str(item.get("id")) for item in response.get("data", []) if item.get("id")]

    def ask(self, name: str, prompt: str, model: str | None = None) -> str:
        provider = self.provider(name)
        if provider.get("kind") != "model":
            raise ProviderError(f"`model ask` requires a model provider, not {name}.")
        if not provider.get("enabled"):
            raise ProviderError(f"Provider is disabled: {name}. Run `continuum providers add {name}`.")
        selected_model = model or provider.get("chat_model") or provider.get("default_model")
        result = self._json_request(
            provider["base_url"].rstrip("/") + "/chat/completions",
            {
                "model": selected_model,
                "messages": [{"role": "user", "content": compact_text(prompt, 12_000)}],
                "max_tokens": 800,
            },
            self._headers(name, provider),
        )
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("Provider returned no chat completion content.") from error

    def embed(self, name: str, text: str, model: str | None = None) -> tuple[str, list[float]]:
        provider = self.provider(name)
        if name != "ollama":
            raise ProviderError("Embedding storage currently supports Ollama only.")
        selected_model = model or provider.get("embedding_model")
        result = self._json_request(
            provider["base_url"].rstrip("/") + "/embeddings",
            {"model": selected_model, "input": compact_text(text, 24_000)},
            self._headers(name, provider),
        )
        try:
            return str(selected_model), [float(item) for item in result["data"][0]["embedding"]]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("Provider returned no embedding vector.") from error

    def run_agent(self, name: str, prompt: str, project: Path) -> str:
        provider = self.provider(name)
        if provider.get("kind") != "agent":
            raise ProviderError(f"`run_agent` requires an agent provider, not {name}.")
        if not provider.get("enabled"):
            raise ProviderError(f"Provider is disabled: {name}. Run `continuum providers add {name}`.")
        command = str(provider.get("command", name))
        executable = shutil.which(command)
        if not executable:
            raise ProviderError(f"Agent CLI not found: {command}. Install it and run `continuum providers test {name}`.")
        invocation = [executable, *[str(item) for item in provider.get("prompt_args", [])], compact_text(prompt, 24_000)]
        if executable.lower().endswith(".ps1"):
            invocation = ["powershell", "-ExecutionPolicy", "Bypass", "-File", *invocation]
        try:
            completed = subprocess.run(
                invocation,
                cwd=str(project),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=int(provider.get("timeout_seconds", 900)),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ProviderError(f"{name} exceeded its configured timeout. Increase timeout_seconds or inspect the agent session.") from error
        except OSError as error:
            raise ProviderError(f"Could not launch {name}: {error}. Run `continuum providers test {name}`.") from error
        output = compact_text((completed.stdout or "") + (completed.stderr or ""), 12_000)
        if completed.returncode != 0:
            raise ProviderError(f"{name} exited with code {completed.returncode}: {output or 'no output'}")
        return output or f"{name} completed without text output."
