"""``nebuko init`` writes two keys into ``.nebuko/nebuko.yaml`` without losing anything (issue #1475)."""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.init.core import MANIFEST_PATH, ManifestMergeError, merge_manifest_settings
from nebuko.engine.init.github_init_client import GhInitClient
from nebuko.engine.init.core import SecretTarget
from nebuko.engine.init.ports import SetupError

EXISTING = """\
# Engine settings for this repo.
default_workflow: default   # the shipped chain

# Scripts that run tests merge the base branch first.
merge_main: true

base_branch: develop
features:
  # Off by default.
  secret_scan: false
"""


def test_only_the_two_keys_change_and_comments_and_order_survive():
    merged = merge_manifest_settings(EXISTING, base_branch="main", test_command="uv run pytest -q")

    assert merged == EXISTING.replace("base_branch: develop\n", "base_branch: main\n") + (
        "test_command: uv run pytest -q\n"
    )


def test_a_blank_test_command_leaves_an_existing_one_alone():
    existing = "test_command: make test\n"

    merged = merge_manifest_settings(existing, base_branch="main", test_command="  ")

    assert merged == "test_command: make test\nbase_branch: main\n"


def test_a_missing_file_gets_just_the_two_keys():
    merged = merge_manifest_settings(None, base_branch="main", test_command="make test")

    assert merged == "base_branch: main\ntest_command: make test\n"


def test_a_command_with_yaml_syntax_is_quoted_so_it_stays_one_value():
    merged = merge_manifest_settings(None, base_branch="main", test_command="echo a: b # c")

    assert merged.count("\n") == 2
    assert "test_command: 'echo a: b # c'" in merged


def test_a_key_on_a_line_with_a_comment_is_refused_with_the_manual_snippet():
    existing = "base_branch: develop  # the release line\n"

    with pytest.raises(ManifestMergeError) as raised:
        merge_manifest_settings(existing, base_branch="main", test_command="make test")

    assert "base_branch: main" in raised.value.snippet
    assert "test_command: make test" in raised.value.snippet
    assert MANIFEST_PATH in str(raised.value)


def test_a_manifest_that_is_not_a_mapping_is_refused():
    with pytest.raises(ManifestMergeError):
        merge_manifest_settings("- not\n- a mapping\n", base_branch="main", test_command="")


def test_invalid_yaml_is_refused():
    with pytest.raises(ManifestMergeError):
        merge_manifest_settings("merge_main: [unclosed\n", base_branch="main", test_command="")


@pytest.mark.parametrize("hostile", ["main; touch PWNED", "a b", "-x"])
def test_a_hostile_base_branch_is_refused_before_anything_is_written(hostile):
    with pytest.raises(ValueError):
        merge_manifest_settings(EXISTING, base_branch=hostile, test_command="")


def _client(returncode: int, stdout: str = "", stderr: str = ""):
    def runner(args, **_kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    return GhInitClient(runner=runner)


TARGET = SecretTarget(scope="repo", repo="acme/widgets")


def test_read_file_decodes_the_contents_api_body():
    import base64

    encoded = base64.b64encode(b"base_branch: main\n").decode()

    assert _client(0, stdout=encoded + "\n").read_file(TARGET, MANIFEST_PATH) == "base_branch: main\n"


def test_read_file_is_none_only_on_a_404():
    assert _client(1, stderr="gh: Not Found (HTTP 404)").read_file(TARGET, MANIFEST_PATH) is None


def test_read_file_raises_on_other_failures_so_an_unreadable_manifest_is_never_overwritten():
    with pytest.raises(SetupError):
        _client(1, stderr="gh: HTTP 502 Bad Gateway").read_file(TARGET, MANIFEST_PATH)
