"""Repo-wide pytest configuration (applies to both ``testpaths``).

``tests/conftest.py`` only covers ``tests/``; the co-located unit tests under
``src/nebuko/engine/lib`` need the same isolation, so the fixture lives here.
"""

from __future__ import annotations

import pytest

from nebuko.engine.lib.resolve_workflow import STEP_PROMPT_DIR_ENV


@pytest.fixture(autouse=True)
def _step_prompt_file_is_private_to_each_test(tmp_path, monkeypatch):
    """Give every test its own step prompt directory.

    The resolver writes the step prompt to one fixed path (``/tmp/step_prompt.md``)
    and removes it when a step has no prompt. With ``pytest -n`` two workers
    resolving workflows at once raced on that file: one saw it vanish between
    its ``exists`` and ``remove`` (``FileNotFoundError``) or read another
    test's prompt. A per-test directory makes the tests independent of the
    worker they land on and of any file a previous run left in ``/tmp``.
    """
    monkeypatch.setenv(STEP_PROMPT_DIR_ENV, str(tmp_path))
