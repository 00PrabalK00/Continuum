"""A lane claimed as a directory has to cover the files inside it.

Matching a changed file against claimed paths by exact string meant that
claiming `src` covered nothing under `src`, so every nested change was reported
as an out-of-scope edit and carried a risk into evidence, flight records and
ROI. The end-to-end demo flow worked around it by claiming precise file paths,
which is the workaround that hid the bug rather than a use of the feature.

Issue #48.
"""

import unittest

import tempfile
from pathlib import Path

from continuum.core import MemoryStore
from continuum.evidence import covered


class LaneScopeTest(unittest.TestCase):
    def test_a_directory_claim_covers_a_file_inside_it(self):
        self.assertTrue(covered("src/app.py", {"src"}))

    def test_a_directory_claim_covers_a_deeply_nested_file(self):
        self.assertTrue(covered("src/api/routes/billing.py", {"src"}))

    def test_a_sibling_directory_with_a_shared_prefix_is_not_covered(self):
        # The reason this matches segment by segment rather than by string
        # prefix: "src-other".startswith("src") is true and wrong.
        self.assertFalse(covered("src-other/x.py", {"src"}))
        self.assertFalse(covered("srcx/app.py", {"src"}))

    def test_an_exact_file_claim_still_matches(self):
        self.assertTrue(covered("src/app.py", {"src/app.py"}))

    def test_an_unrelated_path_is_not_covered(self):
        self.assertFalse(covered("docs/guide.md", {"src"}))

    def test_a_windows_separator_in_the_changed_path_still_matches(self):
        self.assertTrue(covered("src\\app.py", {"src"}))

    def test_a_windows_separator_in_the_claim_still_matches(self):
        self.assertTrue(covered("src/app.py", {"src\\api"} | {"src"}))

    def test_a_trailing_separator_on_the_claim_is_ignored(self):
        self.assertTrue(covered("src/app.py", {"src/"}))

    def test_any_one_of_several_claims_is_enough(self):
        self.assertTrue(covered("tests/test_billing.py", {"src", "tests"}))

    def test_no_claims_covers_nothing(self):
        self.assertFalse(covered("src/app.py", set()))

    def test_an_absolute_claim_does_not_cover_a_relative_path(self):
        # Stripping the leading separator would make /src and src the same
        # claim, so a claim pointing outside the project could suppress the
        # warning for an edit inside it.
        self.assertFalse(covered("src/app.py", {"/src"}))
        self.assertFalse(covered("/src/app.py", {"src"}))

    def test_an_absolute_claim_still_covers_an_absolute_path(self):
        self.assertTrue(covered("/src/app.py", {"/src"}))

    def test_an_empty_claim_does_not_cover_everything(self):
        # A claim recorded as "" or "/" would otherwise match every path and
        # silently disable out-of-scope reporting altogether.
        self.assertFalse(covered("src/app.py", {""}))
        self.assertFalse(covered("src/app.py", {"/"}))


class ClaimOverlapTest(unittest.TestCase):
    """A directory claim owns everything beneath it, so two tasks holding `src`
    and `src/app.py` would both present the same file as in scope. The
    exclusive-claim model has to reject that where claims are created, not
    discover it later."""

    def store(self, temporary):
        store = MemoryStore(Path(temporary) / "repo")
        store.initialize(100000, 0.8)
        store.create_task("first task")
        store.create_task("second task")
        return store

    def test_a_nested_claim_under_someone_elses_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.claim_files("T0001", "claude", ["src"])
            with self.assertRaises(ValueError) as caught:
                store.claim_files("T0002", "codex", ["src/app.py"])
            self.assertIn("overlaps", str(caught.exception))

    def test_a_directory_claim_over_someone_elses_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.claim_files("T0001", "claude", ["src/app.py"])
            with self.assertRaises(ValueError):
                store.claim_files("T0002", "codex", ["src"])

    def test_an_exact_duplicate_is_still_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.claim_files("T0001", "claude", ["src/app.py"])
            with self.assertRaises(ValueError) as caught:
                store.claim_files("T0002", "codex", ["src/app.py"])
            self.assertIn("already claimed", str(caught.exception))

    def test_a_sibling_directory_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.claim_files("T0001", "claude", ["src"])
            store.claim_files("T0002", "codex", ["src-other"])

    def test_the_same_task_can_extend_its_own_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            store.claim_files("T0001", "claude", ["src"])
            store.claim_files("T0001", "claude", ["src/app.py"])

    def test_an_absolute_claim_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(temporary)
            with self.assertRaises(ValueError) as caught:
                store.claim_files("T0001", "claude", ["/etc/passwd"])
            self.assertIn("relative to the project", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
