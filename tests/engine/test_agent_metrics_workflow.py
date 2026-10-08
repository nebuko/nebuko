"""Regression guard for the agent-metrics script path (issue #788).

The engine restructure (#741) relocated the runtime from ``engine/lib/`` to
``src/nebuko/engine/lib/`` but left the ``agent-metrics`` GitHub Actions job
pointing at the old path, so the job died on issue close with
``can't open file .../engine/lib/agent_metrics.py``. This pins the job's
declared ``SCRIPT_PATH`` values to files that actually exist in the checkout.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"
CACHE_PREFIX = ".nebuko-cache/"
SCRIPT_PATH_RE = re.compile(r'SCRIPT_PATH="([^"]+)"')


def _agent_metrics_run_script() -> str:
    workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["agent-metrics"]["steps"]
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
