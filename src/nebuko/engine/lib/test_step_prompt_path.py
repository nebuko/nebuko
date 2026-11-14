"""Where the resolver writes the step prompt file, and why tests can move it."""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.lib.resolve_workflow import (
    STEP_PROMPT_DIR_ENV,
    _write_step_prompt_file,
    step_prompt_path,
)

#: The documented production location (docs/workflows.md, issue #551).
PRODUCTION_PATH = "/tmp/step_prompt.md"


class TestStepPromptPath:
    def test_given_no_override_then_the_documented_path_is_used(self, monkeypatch):
        monkeypatch.delenv(STEP_PROMPT_DIR_ENV, raising=False)
        assert step_prompt_path() == PRODUCTION_PATH

    def test_given_an_override_dir_then_the_file_lives_there(self, tmp_path):
        # the autouse fixture in the repo-root conftest.py sets the override
        assert step_prompt_path() == str(tmp_path / "step_prompt.md")

    def test_given_a_prompt_then_it_is_written_to_the_resolved_path(self, tmp_path):
        assert _write_step_prompt_file("do it\n") == str(tmp_path / "step_prompt.md")
        assert (tmp_path / "step_prompt.md").read_text() == "do it\n"

    @pytest.mark.parametrize("prompt", [None, "", "   \n"])
    def test_given_no_prompt_then_a_stale_file_is_removed(self, tmp_path, prompt):
        stale = tmp_path / "step_prompt.md"
        stale.write_text("stale")
        assert _write_step_prompt_file(prompt) == ""
        assert not stale.exists()

    @pytest.mark.parametrize("prompt", [None, ""])
    def test_given_no_prompt_and_no_file_then_it_does_not_raise(self, tmp_path, prompt):
        # The old exists()-then-remove() raised FileNotFoundError when another
        # worker removed the file in between; removal must tolerate a missing file.
        assert not Path(step_prompt_path()).exists()
        assert _write_step_prompt_file(prompt) == ""
