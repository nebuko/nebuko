"""Spec-scenario tests for packaging engine/lib as ``nebuko.engine.lib`` (issue #1165).

Given the repo with ``src/nebuko/engine/lib`` shipped as a regular subpackage,
importing lib modules and running the suite must work with no ``sys.path``
configuration anywhere.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import nebuko.engine.paths as paths

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestLibIsImportableAsPackage:
    def test_importing_workflow_state_works_with_no_sys_path_mutation(self):
        """Given a clean interpreter, when importing nebuko.engine.lib.workflow_state,
        then it resolves through the installed package and lib dir is never on sys.path."""
        code = (
            "import sys;"
            "import nebuko.engine.lib.workflow_state;"
            "lib = str(nebuko.engine.paths.LIB_DIR);"
            "assert lib not in sys.path, f'{lib} leaked onto sys.path';"
            "print('ok')"
        )
        result = subprocess.run(
            ["uv", "run", "python", "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_lib_dir_is_a_real_subpackage_of_nebuko(self):
        import nebuko.engine.lib

        assert nebuko.engine.lib.__file__.endswith("engine/lib/__init__.py")


class TestNoSysPathInjection:
    def test_paths_module_has_no_ensure_import_path(self):
        """Given the sys.path machinery is removed, when importing
        nebuko.engine.paths, then ensure_import_path no longer exists."""
        assert not hasattr(paths, "ensure_import_path")

    def test_importing_paths_has_no_sys_path_side_effect(self):
        code = (
            "import sys;"
            "before = list(sys.path);"
            "import nebuko.engine.paths;"
            "assert sys.path == before, 'importing nebuko.engine.paths mutated sys.path';"
            "print('ok')"
        )
        result = subprocess.run(
            ["uv", "run", "python", "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_no_sys_path_insert_or_append_anywhere_in_src_or_tests(self):
        """Given the invariant of zero sys.path mutation, when scanning src/ and
        tests/, then no sys.path.insert/append call remains."""
        offenders = []
        for root in ("src", "tests"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8")
                if "sys.path.insert" in text or "sys.path.append" in text:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []
