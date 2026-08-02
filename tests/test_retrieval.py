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


class RankFusionTest(unittest.TestCase):
    """Semantic search returns its full quota of candidates whatever their
    similarity, so taking that list first fills every slot and an exact wording
    match disappears. Enabling embeddings would then make search worse."""

    def events(self, *ids):
        return [{"id": i, "created_at": "t", "kind": "handoff", "payload": {"task": f"e{i}"}} for i in ids]

    def test_an_exact_match_survives_a_full_semantic_list(self):
        semantic = self.events(*range(1, 9))
        lexical = self.events(99, 1, 2, 3)
        ids = [e["id"] for e in retrieval.merge([semantic, lexical], 8)]
        self.assertIn(99, ids)

    def test_an_item_both_sources_like_ranks_above_one_only_a_single_source_likes(self):
        semantic = self.events(5, 1, 2)
        lexical = self.events(5, 7, 8)
        ids = [e["id"] for e in retrieval.merge([semantic, lexical], 3)]
        self.assertEqual(ids[0], 5)

    def test_neither_source_is_starved(self):
        semantic = self.events(*range(1, 21))
        lexical = self.events(*range(100, 120))
        ids = [e["id"] for e in retrieval.merge([semantic, lexical], 10)]
        self.assertTrue(any(i < 100 for i in ids))
        self.assertTrue(any(i >= 100 for i in ids))

    def test_results_are_not_duplicated(self):
        both = self.events(1, 2, 3)
        ids = [e["id"] for e in retrieval.merge([both, both], 5)]
        self.assertEqual(ids, sorted(set(ids)))

    def test_an_empty_source_is_harmless(self):
        ids = [e["id"] for e in retrieval.merge([[], self.events(1, 2)], 5)]
        self.assertEqual(ids, [1, 2])


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


class IncrementalEmbeddingTest(unittest.TestCase):
    """Indexing only at setup leaves every later decision invisible to
    meaning-based search, while setup still reports that search matches
    meaning."""

    def store(self, temporary):
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(100000, 0.8)
        return store

    def test_a_new_handoff_is_embedded_when_the_project_uses_embeddings(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.store_embedding("M:0", "ollama", "m", [0.1, 0.2], "seed")
            with patch("continuum.providers.ProviderManager.embed", return_value=("m", [0.3, 0.4])):
                store.event("handoff", {"task": "chose Redis for the rate limiter", "next_step": "wire it"})
            handoff = next(e for e in reversed(store.recent_events(20)) if e["kind"] == "handoff")
            self.assertTrue(store.has_embedding(f"M:{handoff['id']}"))

    def test_projects_without_embeddings_are_left_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with patch("continuum.providers.ProviderManager.embed") as embed:
                store.event("handoff", {"task": "chose Redis", "next_step": "wire it"})
            embed.assert_not_called()

    def test_an_unreachable_model_does_not_break_recording(self):
        from continuum.providers import ProviderError

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.store_embedding("M:0", "ollama", "m", [0.1], "seed")
            with patch("continuum.providers.ProviderManager.embed", side_effect=ProviderError("down")):
                store.event("handoff", {"task": "chose Redis", "next_step": "wire it"})
            self.assertTrue(any(e["kind"] == "handoff" for e in store.recent_events(10)))

    def test_non_handoff_events_are_not_embedded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.store_embedding("M:0", "ollama", "m", [0.1], "seed")
            with patch("continuum.providers.ProviderManager.embed") as embed:
                store.event("agent_exit", {"summary": "done", "returncode": 0})
            embed.assert_not_called()

    def test_setup_indexing_reaches_past_the_recent_event_window(self):
        from continuum import setup_ui

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.event("handoff", {"task": "chose Redis for the rate limiter", "next_step": "wire it"})
            for index in range(300):
                store.event("agent_exit", {"summary": f"session {index}", "returncode": 0})
            with patch("continuum.providers.ProviderManager.embed", return_value=("m", [0.5])):
                report = setup_ui.index_existing_memory(store)
            self.assertIn("indexed 1", report)

    def test_setup_indexing_does_not_redo_work(self):
        from continuum import setup_ui

        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.event("handoff", {"task": "chose Redis", "next_step": "wire it"})
            with patch("continuum.providers.ProviderManager.embed", return_value=("m", [0.5])):
                setup_ui.index_existing_memory(store)
                report = setup_ui.index_existing_memory(store)
            self.assertIn("indexed 0", report)
