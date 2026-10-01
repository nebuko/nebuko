"""Spec-scenario tests for the import-path bootstrap (issue #590, section 3.7)."""

from __future__ import annotations

import sys

from engine.paths import SCRIPTS_DIR, ensure_import_path, repo_root


class TestRepoRoot:
    def test_points_at_the_checkout_that_holds_the_engine_package(self):
        root = repo_root()
        assert (root / "engine" / "__init__.py").is_file()
        assert (root / "adapters" / "base.py").is_file()

    def test_is_independent_of_the_current_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert (repo_root() / "engine" / "__init__.py").is_file()


class TestScriptsDir:
    def test_points_at_the_legacy_orchestration_scripts(self):
        assert SCRIPTS_DIR.is_dir()
        assert (SCRIPTS_DIR / "event_normalizer.py").is_file()


class TestEnsureImportPath:
    def test_makes_scripts_importable(self, monkeypatch):
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(SCRIPTS_DIR)])
        assert str(SCRIPTS_DIR) not in sys.path

        ensure_import_path()

        assert str(SCRIPTS_DIR) in sys.path
        assert str(SCRIPTS_DIR) in sys.path

    def test_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(SCRIPTS_DIR)])

        ensure_import_path()
        ensure_import_path()

        assert sys.path.count(str(SCRIPTS_DIR)) == 1
