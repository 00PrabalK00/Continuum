"""Interactive setup for `continuum install`.

Setting Continuum up used to mean knowing which optional pieces exist and which
commands turn them on. This asks instead, in a terminal, and does the work.

Everything here degrades to the non-interactive path when there is no terminal
attached, so scripts and CI keep working unchanged.

The one thing this deliberately does not do is install software behind the
user's back. Pulling an embedding model into an Ollama that is already on the
machine is fair game, because that is what Ollama is for. Downloading and
installing Ollama itself is not, so that case prints the address and stops.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .core import MemoryStore

OLLAMA_HOST = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_SITE = "https://ollama.com/download"


def interactive(stream=None) -> bool:
    stream = stream or sys.stdin
    try:
        return bool(stream.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def ask_yes_no(question: str, default: bool = True, reader: Callable[[str], str] | None = None) -> bool:
    reader = reader or input
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = reader(f"{question} {suffix} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("  Please answer y or n.")


def choose(question: str, options: list[tuple[str, str]], reader: Callable[[str], str] | None = None) -> str:
    """Ask for one option. Returns the chosen key."""
    reader = reader or input
    print(question)
    for index, (_key, label) in enumerate(options, start=1):
        print(f"  {index}. {label}")
    while True:
        try:
            answer = reader("Choose a number [1] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return options[0][0]
        if not answer:
            return options[0][0]
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print(f"  Enter a number between 1 and {len(options)}.")


def ollama_running(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ollama_models(timeout: float = 3.0) -> list[str]:
    import json

    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [str(item.get("name", "")) for item in payload.get("models", [])]


def start_ollama(wait_seconds: int = 20) -> bool:
    """Start a local Ollama server and wait for it to answer."""
    executable = shutil.which("ollama")
    if not executable:
        return False
    try:
        subprocess.Popen(
            [executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return False
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if ollama_running():
            return True
        time.sleep(1)
    return False


def install_command() -> tuple[list[str], str] | None:
    """The command that installs Ollama on this platform, if there is a safe one.

    Package managers are preferred because the user can audit and undo them the
    same way as anything else they installed. Linux has no single package
    manager for this, so it falls back to the vendor's own script, which is the
    documented install path.
    """
    if sys.platform == "win32":
        if shutil.which("winget"):
            return (
                ["winget", "install", "--id", "Ollama.Ollama", "-e",
                 "--accept-package-agreements", "--accept-source-agreements"],
                "winget install --id Ollama.Ollama",
            )
        return None
    if sys.platform == "darwin":
        if shutil.which("brew"):
            return ["brew", "install", "ollama"], "brew install ollama"
        return None
    if shutil.which("curl") and shutil.which("sh"):
        return (
            ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            "curl -fsSL https://ollama.com/install.sh | sh",
        )
    return None


def offer_install(reader: Callable[[str], str] | None = None) -> bool:
    """Ask whether to install Ollama, and install it if the answer is yes.

    The exact command is printed before the question, so agreeing is agreeing to
    something specific rather than to the word "install".
    """
    command = install_command()
    if command is None:
        print("  No package manager available here to install Ollama automatically.")
        print(f"  Install it from {OLLAMA_SITE} and rerun `continuum install`.")
        return False
    arguments, shown = command
    print("  Ollama is not installed. It runs the embedding model locally,")
    print("  so your project text never leaves the machine.")
    print(f"  This would run: {shown}")
    if not ask_yes_no("  Install Ollama now?", False, reader):
        print(f"  Skipped. You can install it later from {OLLAMA_SITE}")
        return False
    print("  Installing. This can take a few minutes.")
    try:
        completed = subprocess.run(arguments, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"  Install did not finish ({error}).")
        return False
    if completed.returncode != 0:
        print(f"  Install did not finish (exit code {completed.returncode}).")
        print(f"  Install it manually from {OLLAMA_SITE}")
        return False
    return shutil.which("ollama") is not None


def pull_model(model: str = EMBEDDING_MODEL) -> tuple[bool, str]:
    executable = shutil.which("ollama")
    if not executable:
        return False, "ollama is not on PATH"
    print(f"  Downloading {model}. This runs once and takes a few minutes.")
    try:
        completed = subprocess.run([executable, "pull", model], capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return False, detail[-1] if detail else f"exit code {completed.returncode}"
    return True, model


def enable_semantic_search(store: "MemoryStore", reader: Callable[[str], str] | None = None) -> str:
    """Offer to set up local embeddings, and do it if the user agrees.

    Returns a one-line report of what happened.
    """
    if not shutil.which("ollama") and not offer_install(reader):
        return "search matches wording only; Ollama not installed"

    if not ollama_running():
        print("  Ollama is installed but not running. Starting it.")
        if not start_ollama():
            return "could not start Ollama; search matches wording only"

    if not any(name.startswith(EMBEDDING_MODEL) for name in ollama_models()):
        if not ask_yes_no(f"  Download the {EMBEDDING_MODEL} model (about 275 MB)?", True, reader):
            return "skipped the embedding model; search matches wording only"
        ok, detail = pull_model()
        if not ok:
            return f"could not download {EMBEDDING_MODEL} ({detail}); search matches wording only"

    from .providers import ProviderError, ProviderManager

    try:
        ProviderManager(store.state_dir, store).add("ollama")
    except (ProviderError, ValueError) as error:
        return f"could not enable the Ollama provider ({error})"
    return "search matches wording and meaning"


def index_existing_memory(store: "MemoryStore") -> str:
    from .providers import ProviderError, ProviderManager

    manager = ProviderManager(store.state_dir, store)
    events = [item for item in store.recent_events(200) if item["kind"] == "handoff"]
    if not events:
        return "no recorded memory to index yet"
    indexed = 0
    for item in events:
        text = str(item["payload"].get("task") or "")
        if not text:
            continue
        try:
            model, vector = manager.embed("ollama", text)
        except (ProviderError, OSError):
            return f"indexed {indexed} entries before the embedding model stopped responding"
        store.store_embedding(f"M:{item['id']}", "ollama", model, vector, text)
        indexed += 1
    return f"indexed {indexed} recorded entries"


def configure_handoff_model(store: "MemoryStore", reader: Callable[[str], str] | None = None) -> str:
    """Offer a small model that writes the handoff summaries."""
    options = [("none", "No, keep the recorded summary (default)")]
    if shutil.which("ollama") and ollama_running():
        options.append(("ollama", "Yes, use a local Ollama model"))
    import os

    if os.environ.get("OPENROUTER_API_KEY"):
        options.append(("openrouter", "Yes, use OpenRouter (sends session text off this machine)"))
    if len(options) == 1:
        return "handoff summaries use recorded state"

    chosen = choose(
        "  Should a small model write your session summaries?", options, reader
    )
    if chosen == "none":
        return "handoff summaries use recorded state"

    from .handoff_llm import write_handoff_model
    from .providers import ProviderError, ProviderManager

    try:
        ProviderManager(store.state_dir, store).add(chosen)
    except (ProviderError, ValueError) as error:
        return f"could not enable {chosen} ({error})"
    write_handoff_model(store, chosen, None)
    return f"handoff summaries written by {chosen}"
