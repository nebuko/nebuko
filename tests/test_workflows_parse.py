"""Every workflow YAML shipped or overridden must parse as safe YAML."""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".nebuko" / "workflows"


def test_all_workflow_yaml_files_parse() -> None:  # @stub-protected: signature defined by architect — do not modify
    files = sorted(
        p
        for pattern in ("*.yaml", "*.yml")
        for p in WORKFLOWS_DIR.rglob(pattern)
    )
    assert files, f"no workflow YAML files found under {WORKFLOWS_DIR}"

    for path in files:
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            pytest.fail(f"{path} does not parse: {exc}")
