"""The configured base branch resolves in one place (issue #1452)."""

from __future__ import annotations

from nebuko.engine.lib.git.base_branch import DEFAULT_BASE_BRANCH, resolve_base_branch
from nebuko.engine.models import NebukoManifest


def _no_head() -> None:
    return None


def test_manifest_base_branch_maps_to_env() -> None:
    manifest = NebukoManifest(base_branch=" master ")
    assert manifest.env_defaults() == {"NEBUKO_BASE_BRANCH": "master"}


def test_blank_manifest_base_branch_is_unset() -> None:
    assert NebukoManifest(base_branch="  ").env_defaults() == {}


def test_manifest_base_branch_defers_to_explicit_env() -> None:
    merged = NebukoManifest(base_branch="master").overlay_env({"NEBUKO_BASE_BRANCH": "trunk"})
    assert merged["NEBUKO_BASE_BRANCH"] == "trunk"


def test_resolver_precedence() -> None:
    head = lambda: "origin/develop"  # noqa: E731
    env = {"NEBUKO_BASE_BRANCH": "master", "GITHUB_BASE_REF": "release"}
    assert resolve_base_branch(env, head) == "master"
    assert resolve_base_branch({"GITHUB_BASE_REF": "release"}, head) == "release"
    assert resolve_base_branch({}, head) == "develop"
    assert resolve_base_branch({}, _no_head) == DEFAULT_BASE_BRANCH


def test_blank_env_values_fall_through() -> None:
    env = {"NEBUKO_BASE_BRANCH": "  ", "GITHUB_BASE_REF": ""}
    assert resolve_base_branch(env, _no_head) == DEFAULT_BASE_BRANCH
