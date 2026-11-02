"""Every bundled workflow YAML must parse (regression for unquoted colons)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".nebuko" / "workflows"

# @stub-protected: signature defined by architect — do not modify
def test_all_workflow_yaml_files_parse() -> None:
    yaml_files = sorted(WORKFLOWS_DIR.rglob("*.yaml"))
    assert yaml_files, f"no workflow files found under {WORKFLOWS_DIR}"

    for path in yaml_files:
        with path.open() as f:
            parsed = yaml.safe_load(f)
        assert parsed, f"{path} is empty or does not parse"
