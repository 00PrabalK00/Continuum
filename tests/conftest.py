import sys
import tempfile
import unittest
from pathlib import Path
from typing import Generator
from unittest.mock import patch

from continuum.core import MemoryStore


class ContinuumTestCase(unittest.TestCase):
    """Base test case with shared fixtures for Continuum tests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.project = self.tmp_path / "project"
        self.home = self.tmp_path / "home"
        self.store = MemoryStore(self.project)
        self.store.initialize(1000, 0.8)

    def _cli(self, *args: str) -> int:
        """Run a continuum CLI command and return exit code."""
        previous = sys.argv
        try:
            sys.argv = ["continuum", *args, "--project", str(self.project)]
            from continuum.cli import main
            return main()
        finally:
            sys.argv = previous
