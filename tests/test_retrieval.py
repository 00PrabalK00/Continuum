import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from continuum import retrieval
from continuum.core import MemoryStore
from continuum.providers import ProviderError


def store_with(temporary: str, entries) -> MemoryStore:
    store = MemoryStore(Path(temporary) / "repo")
    store.initialize(100000, 0.8)
    for task, next_step in entries:
        store.event("handoff", {"task": task, "next_step": next_step})
    return store


SAMPLE = [
    ("chose PostgreSQL over MySQL for the audit log", "write the schema"),
    ("fixed the retry helper in billing", "add a test"),
    ("renamed the payment client to BillingGateway", "migrate callers"),
]


def top_task(results):
    return results[0]["payload"].get("task") if results else None


class MatchQueryTest(unittest.TestCase):
    def test_punctuation_cannot_break_the_query_language(self):
        for hostile in ['"unclosed', "a AND (b", "NEAR/2", "-x", "*", "'"]:
            expression = retrieval.match_query(hostile + " retry")
            self.assertIn("retry", expression)

    def test_noise_words_are_dropped_but_never_everything(self):
        self.assertEqual(retrieval.match_query("what is the retry"), '"retry"')
        self.assertEqual(retrieval.match_query("what is the"), '"what" OR "is" OR "the"')

    def test_repeated_words_appear_once(self):
        self.assertEqual(retrieval.match_query("retry retry retry"), '"retry"')


class LexicalSearchTest(unittest.TestCase):
    """Ranked full-text search is the default because SQLite ships FTS5, so it
    needs no model, no service and no download."""

    def test_it_finds_a_different_word_form(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            results, strategy = retrieval.search(store, "payment rename", 3)
            self.assertEqual(strategy, retrieval.LEXICAL)
            self.assertEqual(top_task(results), "renamed the payment client to BillingGateway")

    def test_substring_search_would_have_missed_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            self.assertEqual(store.search("payment rename", 3), [])

    def test_ranking_puts_the_best_match_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            results, _ = retrieval.search(store, "audit log schema", 3)
            self.assertEqual(top_task(results), "chose PostgreSQL over MySQL for the audit log")

    def test_an_empty_query_returns_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            self.assertEqual(retrieval.search(store, "   ", 3)[0], [])

    def test_the_index_catches_up_with_new_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            retrieval.search(store, "audit", 3)
            store.event("handoff", {"task": "adopted OpenTelemetry for tracing", "next_step": "wire it"})
            results, _ = retrieval.search(store, "tracing", 3)
            self.assertEqual(top_task(results), "adopted OpenTelemetry for tracing")

    def test_it_falls_back_when_fts5_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            with patch.object(retrieval, "ensure_index", return_value=False):
                results, strategy = retrieval.search(store, "audit log", 3)
            self.assertEqual(strategy, retrieval.LIKE_ONLY)
            self.assertEqual(top_task(results), "chose PostgreSQL over MySQL for the audit log")


class OptionalSemanticTest(unittest.TestCase):
    """Embeddings improve recall for a paraphrase, and are never required."""

    def test_without_embeddings_search_still_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            self.assertEqual(retrieval.embedding_count(store), 0)
            results, strategy = retrieval.search(store, "audit log", 3)
            self.assertEqual(strategy, retrieval.LEXICAL)
            self.assertTrue(results)

    def test_an_unreachable_embedding_model_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            with (
                patch.object(retrieval, "embedding_count", return_value=4),
                patch.object(retrieval, "semantic_events", side_effect=ProviderError("ollama down")),
            ):
                results, strategy = retrieval.search(store, "audit log", 3)
            self.assertEqual(strategy, retrieval.LEXICAL)
            self.assertTrue(results)

    def test_semantic_hits_are_merged_ahead_of_lexical_ones(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            wanted = store.get_memory(1)
            with (
                patch.object(retrieval, "embedding_count", return_value=4),
                patch.object(retrieval, "semantic_events", return_value=[wanted]),
            ):
                results, strategy = retrieval.search(store, "retry", 3)
            self.assertEqual(strategy, retrieval.HYBRID)
            self.assertEqual(results[0]["id"], wanted["id"])

    def test_the_same_event_is_not_returned_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            duplicate = store.get_memory(2)
            with (
                patch.object(retrieval, "embedding_count", return_value=4),
                patch.object(retrieval, "semantic_events", return_value=[duplicate]),
            ):
                results, _ = retrieval.search(store, "retry helper billing", 5)
            identifiers = [item["id"] for item in results]
            self.assertEqual(len(identifiers), len(set(identifiers)))


class SearchSurfacesTest(unittest.TestCase):
    def test_the_mcp_tool_reports_the_strategy(self):
        from continuum.mcp_server import call_tool

        with tempfile.TemporaryDirectory() as temporary:
            store = store_with(temporary, SAMPLE)
            text = call_tool(store, "search_memory", {"query": "payment rename"})["content"][0]["text"]
            self.assertIn("Matched by", text)
            self.assertIn("BillingGateway", text)

    def test_the_tool_description_tells_agents_to_use_their_own_words(self):
        from continuum.mcp_server import tool_definitions

        tool = next(item for item in tool_definitions() if item["name"] == "search_memory")
        self.assertIn("meaning", tool["description"])


class FtsAvailabilityTest(unittest.TestCase):
    def test_this_python_has_fts5(self):
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        except sqlite3.OperationalError:
            self.skipTest("SQLite built without FTS5; Continuum falls back to substring search")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
