"""Packaging metadata that would only fail at release time otherwise."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pyproject() -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.9 and 3.10
        return {}
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


class VersionSyncTest(unittest.TestCase):
    """Three files carry the version. A release tag is checked against all
    three in CI, but catching a mismatch here is cheaper than at publish time."""

    def versions(self) -> dict:
        found = {
            "package.json": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
            "__init__.py": re.search(
                r'__version__ = "([^"]+)"', (ROOT / "continuum" / "__init__.py").read_text(encoding="utf-8")
            ).group(1),
        }
        data = pyproject()
        if data:
            found["pyproject.toml"] = data["project"]["version"]
        return found

    def test_every_file_agrees_on_the_version(self):
        found = self.versions()
        self.assertEqual(len(set(found.values())), 1, f"version mismatch: {found}")


class PyPiMetadataTest(unittest.TestCase):
    def setUp(self):
        self.data = pyproject()
        if not self.data:
            self.skipTest("tomllib needs Python 3.11 or newer")

    def test_the_project_links_somewhere(self):
        # Without these the PyPI page has no route back to the repository.
        urls = self.data["project"].get("urls", {})
        self.assertIn("Repository", urls)
        self.assertIn("Issues", urls)

    def test_the_readme_logo_is_absolute(self):
        # PyPI does not resolve repository-relative paths, so a relative logo
        # renders as a broken image on the project page.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for match in re.findall(r'<img\s+src="([^"]+)"', readme) + re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme):
            self.assertTrue(
                match.startswith("http"),
                f"README image {match!r} is relative and will break on PyPI",
            )

    def test_the_license_is_declared_for_the_build_backend_in_use(self):
        project = self.data["project"]
        self.assertEqual(project.get("license"), "MIT")
        self.assertEqual(project.get("license-files"), ["LICENSE"])
        # PEP 639 replaced the classifier with the expression above; keeping
        # both makes the build fail.
        self.assertNotIn(
            "License :: OSI Approved :: MIT License", project.get("classifiers", [])
        )

    def test_the_build_backend_is_new_enough_for_that_declaration(self):
        requires = " ".join(self.data["build-system"]["requires"])
        version = int(re.search(r"setuptools>=(\d+)", requires).group(1))
        self.assertGreaterEqual(version, 77, "PEP 639 license expressions need setuptools 77+")

    def test_the_ui_assets_ship(self):
        # control_center.py loads these from beside the module, so a wheel
        # without them starts and then fails when the dashboard is opened.
        globs = self.data["tool"]["setuptools"]["package-data"]["continuum"]
        for asset in (ROOT / "continuum" / "ui").iterdir():
            suffix = asset.suffix
            self.assertTrue(
                any(pattern.endswith(suffix) for pattern in globs),
                f"{asset.name} is not covered by package-data {globs}",
            )


class NpmMetadataTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    def test_the_package_links_somewhere(self):
        for field in ("repository", "homepage", "bugs"):
            self.assertIn(field, self.data)

    def test_the_bin_entry_exists(self):
        for target in self.data["bin"].values():
            self.assertTrue((ROOT / target).exists(), f"bin target {target} is missing")


class ReleaseWorkflowTest(unittest.TestCase):
    def setUp(self):
        path = ROOT / ".github" / "workflows" / "release.yml"
        if not path.exists():
            self.skipTest("no release workflow")
        self.text = path.read_text(encoding="utf-8")

    def test_publishing_is_tag_driven_not_push_driven(self):
        self.assertIn('tags:', self.text)
        self.assertNotIn("branches:", self.text)

    def test_publishing_waits_for_the_tests(self):
        self.assertIn("needs: [test, version-matches-tag]", self.text)

    def test_pypi_uses_trusted_publishing_rather_than_a_stored_token(self):
        self.assertIn("id-token: write", self.text)
        self.assertNotIn("PYPI_API_TOKEN", self.text)


if __name__ == "__main__":
    unittest.main()
