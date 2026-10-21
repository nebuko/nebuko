"""Regression guard for the agent-metrics script path (issue #788).

The engine restructure (#741) relocated the runtime from ``engine/lib/`` to
``src/nebuko/engine/lib/`` but left the ``agent-metrics`` GitHub Actions job
pointing at the old path, so the job died on issue close with
``can't open file .../engine/lib/agent_metrics.py``. This pins the job's
declared ``SCRIPT_PATH`` values to files that actually exist in the checkout.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_LIB = REPO_ROOT / "src" / "nebuko" / "engine" / "lib"
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"
CACHE_PREFIX = ".nebuko-cache/"
SCRIPT_PATH_RE = re.compile(r'SCRIPT_PATH="([^"]+)"')


def _agent_metrics_steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    return workflow["jobs"]["agent-metrics"]["steps"]


def _agent_metrics_run_script() -> str:
    steps = _agent_metrics_steps()
    step = next(s for s in steps if s.get("name") == "Aggregate agent metrics")
    return step["run"]


class TestAgentMetricsScriptPath:
    def test_every_declared_script_path_exists_in_the_checkout(self):
        declared_paths = SCRIPT_PATH_RE.findall(_agent_metrics_run_script())

        assert declared_paths, "expected the job to resolve a SCRIPT_PATH"
        for declared in declared_paths:
            repo_relative = declared.removeprefix(CACHE_PREFIX)
            assert (REPO_ROOT / repo_relative).is_file(), (
                f"agent-metrics points at a missing script: {declared}"
            )


class TestAgentMetricsDependencyProvisioning:
    """Structural guard: agent-metrics must provision deps before running the script.

    The job ran ``python "$SCRIPT_PATH"`` under a bare interpreter with no
    dependency-install step, so it died on ``ModuleNotFoundError: pydantic``
    (issue #923). These tests pin the install step and that it pulls
    dependencies from the engine manifest rather than a hardcoded package list.
    """

    STEP_NAME = "Install project dependencies"
    AGGREGATE_STEP = "Aggregate agent metrics"

    def _install_step(self) -> dict:
        steps = _agent_metrics_steps()
        return next(s for s in steps if s.get("name") == self.STEP_NAME)

    def _step_names(self) -> list[str]:
        return [s.get("name") for s in _agent_metrics_steps()]

    def test_install_step_runs_before_aggregate(self) -> None:
        names = self._step_names()
        assert self.STEP_NAME in names, "agent-metrics must install dependencies"
        assert names.index(self.STEP_NAME) < names.index(self.AGGREGATE_STEP), (
            "dependencies must be installed before the metrics script runs"
        )

    def test_install_uses_manifest_root_not_hardcoded_packages(self) -> None:
        run = self._install_step()["run"]
        assert "pip install" in run
        assert "$PROJECT_ROOT" in run
        assert 'PROJECT_ROOT=".nebuko-cache"' in run
        assert 'PROJECT_ROOT="."' in run
        assert not re.search(r"pip install\s+(?:pyyaml|pydantic|rich)\b", run), (
            "deps must come from the manifest, not a hardcoded package list"
        )


class TestReportTimePricingImportability:
    """The summarize job installs base deps only, so the report-time pricing
    chain must import with ``openhands``/``litellm`` absent (issue #928)."""

    def test_pricing_and_resolver_import_without_openhands(self, tmp_path) -> None:
        script = (
            "import builtins, os, sys;"
            "sys.path[:] = [p for p in sys.path if not p or not os.path.exists("
            "os.path.join(p, 'agent_script'))];"
            f"sys.path.insert(0, {str(ENGINE_LIB)!r});"
            "real = builtins.__import__;"
            "guard = lambda name, *a, **k: (_ for _ in ()).throw("
            "ImportError(name)) if name.split('.')[0] in "
            "('openhands', 'litellm') else real(name, *a, **k);"
            "builtins.__import__ = guard;"
            "import agent_script, pricing;"
            "from agent_script.sub_domains.metrics.report_cost_resolver import"
            " ReportCostResolver;"
            "from pricing.features.load_price_table.load_price_table import"
            " load_price_table;"
            f"assert agent_script.__file__.startswith({str(ENGINE_LIB)!r}),"
            " agent_script.__file__;"
            "print('ok')"
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, result.stderr


def test_resolve_token_prefers_the_workflow_token(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "agent_metrics_under_test", ENGINE_LIB / "agent_metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("GH_TOKEN", "broker-token")
    monkeypatch.setenv("GITHUB_TOKEN", "job-token")
    assert module._resolve_token() == "broker-token"

    monkeypatch.delenv("GH_TOKEN")
    assert module._resolve_token() == "job-token"
