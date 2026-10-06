"""Spec-scenario tests for the import-path bootstrap (issue #590, section 3.7)."""

from __future__ import annotations

import sys

from nebuko.engine.paths import LIB_DIR, ensure_import_path, repo_root


class TestRepoRoot:
    def test_points_at_the_checkout_that_holds_the_engine_package(self):
        root = repo_root()
        assert (root / "src" / "nebuko" / "engine" / "__init__.py").is_file()
        assert (root / "src" / "nebuko" / "adapters" / "base.py").is_file()

    def test_is_independent_of_the_current_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert (repo_root() / "src" / "nebuko" / "engine" / "__init__.py").is_file()


class TestLibDir:
    def test_points_at_the_migrated_orchestration_library(self):
        assert LIB_DIR.is_dir()
        assert (LIB_DIR / "event_normalizer.py").is_file()


class TestEnsureImportPath:
    def test_makes_runtime_importable(self, monkeypatch):
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(LIB_DIR)])
        assert str(LIB_DIR) not in sys.path

        ensure_import_path()

        assert str(LIB_DIR) in sys.path
        assert str(LIB_DIR) in sys.path

    def test_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(LIB_DIR)])

        ensure_import_path()
        ensure_import_path()

        assert sys.path.count(str(LIB_DIR)) == 1
