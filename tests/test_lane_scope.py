"""A lane claimed as a directory has to cover the files inside it.

Matching a changed file against claimed paths by exact string meant that
claiming `src` covered nothing under `src`, so every nested change was reported
as an out-of-scope edit and carried a risk into evidence, flight records and
ROI. The end-to-end demo flow worked around it by claiming precise file paths,
which is the workaround that hid the bug rather than a use of the feature.

Issue #48.
"""

import unittest

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

    def test_an_empty_claim_does_not_cover_everything(self):
        # A claim recorded as "" or "/" would otherwise match every path and
        # silently disable out-of-scope reporting altogether.
        self.assertFalse(covered("src/app.py", {""}))
        self.assertFalse(covered("src/app.py", {"/"}))


if __name__ == "__main__":
    unittest.main()
