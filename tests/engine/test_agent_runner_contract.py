"""Real-code-path coverage for the legacy agent seam (issue #590, review round 3).

The driver does not own an LLM runtime: :func:`engine.agent_runner.run_legacy_agent`
spawns the *actual* ``.github/scripts/agent_script/agent_script.py``. These tests
exercise that seam for real — entry point, child cwd, ``PYTHONPATH`` and artefact
names — without ever reaching the LLM (the real script is driven into its own
argument-validation error, and the artefact layout is proven with a stub entry
point written to the same relative path).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.agent_runner import (
    _FINAL_MESSAGE_FILE,
    _LATEST_ANSWER_FILE,
    _STATE_FILE,
    run_legacy_agent,
)
from engine.paths import ensure_import_path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = Path(".github") / "scripts" / "agent_script" / "agent_script.py"


def _stub_entry_point(script: Path) -> None:
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import json, pathlib, sys\n"
        "prompt = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "pathlib.Path('latest_answer.md').write_text(prompt, encoding='utf-8')\n"
        "pathlib.Path('final_message.txt').write_text('final: ' + prompt, encoding='utf-8')\n"
        "state = {'version': 1, 'workflow': 'spec-driven-impl', 'step': 'stub', 'agent': 'dev'}\n"
        "pathlib.Path('workflow_state.json').write_text(json.dumps(state), encoding='utf-8')\n"
        "print('stub entry point ran from', pathlib.Path.cwd())\n",
        encoding="utf-8",
    )


def test_real_entry_point_imports_its_package_and_rejects_conflicting_prompts() -> None:
    """The real entry point must get past its imports under the seam's child env.

    ``PROMPT_STRING`` makes the script exit 1 on its own validation *after* it
    imported ``.github/scripts/agent_script`` — so reaching that message proves the
    staged entry point plus ``PYTHONPATH`` reproduce the legacy invariant. Running
    the script in place instead fails earlier with
    ``cannot import name 'load_prompt' from 'agent_script'``.
    """
    with pytest.raises(RuntimeError) as excinfo:
        run_legacy_agent("prompt", repo_root=REPO_ROOT, env={"PROMPT_STRING": "conflict"})

    message = str(excinfo.value)
    assert "Both PROMPT_STRING and prompt_location provided" in message
    assert "cannot import name" not in message


def test_artifacts_are_read_back_from_the_child_working_directory(tmp_path: Path) -> None:
    """Answer, final message and state come from the child's cwd, by legacy name."""
    repo_root = tmp_path / "checkout"
    _stub_entry_point(repo_root / ENTRY_POINT)

    result: dict[str, Any] = run_legacy_agent("hello engine", repo_root=repo_root)

    assert result["answer"] == "hello engine"
    assert result["final_message"] == "final: hello engine"
    assert result["state"].version == 1
    assert result["state"].agent == "dev"
    assert "stub entry point ran from" in result["console_log"]


def test_state_file_missing_is_reported_as_none(tmp_path: Path) -> None:
    """A run that never wrote state must not invent one (review round 2, item 3)."""
    repo_root = tmp_path / "checkout"
    script = repo_root / ENTRY_POINT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path('latest_answer.md').write_text("
        "pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = run_legacy_agent("no state here", repo_root=repo_root)

    assert result["state"] is None
    assert result["final_message"] == ""


def test_artifact_names_match_the_legacy_readers() -> None:
    """The seam's file names are the ones the legacy host and scripts already use."""
    ensure_import_path()
    import prepare_comment

    assert _LATEST_ANSWER_FILE == prepare_comment.LATEST_ANSWER_FILE

    build_output = REPO_ROOT / ".github/scripts/agent_script/features/build_output/build_output.py"
    build_output_source = build_output.read_text(encoding="utf-8")
    assert f'"{_FINAL_MESSAGE_FILE}"' in build_output_source
    assert f'"{_STATE_FILE}"' in build_output_source
