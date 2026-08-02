"""Find recorded memory by wording, and by meaning when that is available.

The search an agent reaches through MCP used to be a SQL `LIKE` over the event
payload. That answers "which event contains the word retry" and nothing else,
with no ranking: an agent asking what was decided about storage gets nothing
back unless someone happened to write "storage".

Ranked full-text search fixes most of that for free. SQLite ships FTS5 with
BM25 scoring in the standard library, so it needs no model, no service and no
download, and it runs on any machine that can run Continuum.

Embeddings are treated as an optional improvement rather than a requirement.
When a project has them and the local embedding model answers, semantic hits are
merged in ahead of the lexical ones, because the two fail in opposite
directions: BM25 finds identifiers, filenames and error strings that an
embedding blurs together, and embeddings find the paraphrase that shares no
words with the query. When embeddings are absent or the model is unreachable,
search still works and simply says so.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from .providers import ProviderError, ProviderManager

if TYPE_CHECKING:
    from .core import MemoryStore

LIKE_ONLY = "substring match"
LEXICAL = "ranked text match"
HYBRID = "ranked text match and meaning"

TOKEN = re.compile(r"[A-Za-z0-9_]+")
# Words that match nearly every event and only dilute the ranking.
NOISE = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "is", "are",
         "was", "were", "we", "i", "it", "that", "this", "what", "which", "did",
         "do", "does", "with", "about", "from", "our", "my", "be", "been"}


def match_query(query: str) -> str:
    """Turn free text into an FTS5 MATCH expression.

    User text goes straight into a query language that treats quotes, hyphens
    and parentheses as syntax, so only word tokens survive and each one is
    quoted. Tokens are OR'd because recall matters more than precision here:
    BM25 sorts out which hits are actually good.
    """
    tokens = [token.lower() for token in TOKEN.findall(query)]
    meaningful = [token for token in tokens if token not in NOISE] or tokens
    return " OR ".join(f'"{token}"' for token in dict.fromkeys(meaningful))


def ensure_index(connection: sqlite3.Connection) -> bool:
    """Create the full-text index if needed and catch it up to the event log.

    Indexing is incremental: only events newer than the highest indexed row are
    added, so this stays cheap on every search rather than only the first.
    """
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts "
            "USING fts5(body, event_id UNINDEXED)"
        )
    except sqlite3.OperationalError:
        return False
    highest = connection.execute(
        "SELECT COALESCE(MAX(CAST(event_id AS INTEGER)), 0) FROM events_fts"
    ).fetchone()[0]
    rows = connection.execute(
        "SELECT id, kind, payload FROM events WHERE id > ? ORDER BY id", (highest,)
    ).fetchall()
    if rows:
        connection.executemany(
            "INSERT INTO events_fts(body, event_id) VALUES (?, ?)",
            [(f"{row[1]} {row[2]}", row[0]) for row in rows],
        )
        connection.commit()
    return True


def lexical_events(store: "MemoryStore", query: str, limit: int) -> list[dict[str, Any]] | None:
    """Rank events with BM25. Returns None when FTS5 is unavailable."""
    if not store.db_file.exists():
        return []
    expression = match_query(query)
    if not expression:
        return []
    connection = store.connect()
    try:
        if not ensure_index(connection):
            return None
        rows = connection.execute(
            "SELECT e.id, e.created_at, e.kind, e.payload "
            "FROM events_fts f JOIN events e ON e.id = CAST(f.event_id AS INTEGER) "
            "WHERE events_fts MATCH ? ORDER BY bm25(events_fts) LIMIT ?",
            (expression, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    return [
        {"id": row[0], "created_at": row[1], "kind": row[2], "payload": json.loads(row[3])}
        for row in rows
    ]


def embedding_count(store: "MemoryStore") -> int:
    if not store.db_file.exists():
        return 0
    connection = store.connect()
    try:
        return int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        connection.close()


def semantic_events(store: "MemoryStore", query: str, limit: int) -> list[dict[str, Any]]:
    """Rank recorded events by embedding similarity.

    Raises ProviderError when the local embedding model cannot be reached, so
    the caller falls back rather than reporting an empty result as an answer.
    """
    _model, vector = ProviderManager(store.state_dir, store).embed("ollama", query)
    events: list[dict[str, Any]] = []
    for hit in store.semantic_search(vector, limit, query):
        memory_id = str(hit.get("memory_id") or "")
        if not memory_id.startswith("M"):
            continue
        try:
            event = store.get_memory(int(memory_id[1:]))
        except (TypeError, ValueError):
            continue
        if event:
            events.append(event)
    return events


def merge(groups: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[int] = set()
    for group in groups:
        for event in group:
            identifier = int(event.get("id", -1))
            if identifier in seen:
                continue
            seen.add(identifier)
            merged.append(event)
            if len(merged) >= limit:
                return merged
    return merged


def search(store: "MemoryStore", query: str, limit: int = 8) -> tuple[list[dict[str, Any]], str]:
    """Return ranked memory events and the strategy that produced them."""
    query = query.strip()
    if not query:
        return [], LIKE_ONLY

    lexical = lexical_events(store, query, limit)
    if lexical is None:
        # Very old SQLite builds ship without FTS5.
        return store.search(query, limit)[:limit], LIKE_ONLY

    if embedding_count(store) == 0:
        return lexical[:limit], LEXICAL
    try:
        semantic = semantic_events(store, query, limit)
    except (ProviderError, OSError):
        return lexical[:limit], LEXICAL
    if not semantic:
        return lexical[:limit], LEXICAL
    return merge([semantic, lexical], limit), HYBRID
