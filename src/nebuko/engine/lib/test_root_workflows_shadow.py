"""Regression tests: a repo-root workflows/ dir can never shadow defaults/workflows.

Guards the fix for the 15 baseline failures caused by the checkout carrying
both a root `workflows/` dir and `defaults/workflows/`.
"""

from pathlib import Path

from remote_workflow import workflow_candidate_paths


def test_root_workflows_dir_never_shadows_defaults(tmp_path, monkeypatch):
    # given: a fake checkout with a shipped default AND a root workflows/ shadow
    defaults_file = tmp_path / "defaults" / "workflows" / "spec-driven-impl.yaml"
    defaults_file.parent.mkdir(parents=True)
    defaults_file.write_text("defaults")
    root_shadow = tmp_path / "workflows" / "spec-driven-impl.yaml"
    root_shadow.parent.mkdir(parents=True)
    root_shadow.write_text("root-shadow")

    # when
    candidates = workflow_candidate_paths(str(tmp_path), "spec-driven-impl.yaml")

    # then: exactly two candidates, override-then-defaults order, no root shadow
    assert candidates == [
        str(tmp_path / ".nebuko" / "workflows" / "spec-driven-impl.yaml"),
        str(tmp_path / "defaults" / "workflows" / "spec-driven-impl.yaml"),
    ]
    assert str(root_shadow) not in candidates

    # and: the first existing candidate is the defaults file
    existing = [c for c in candidates if Path(c).is_file()]
    assert existing[0] == str(defaults_file)


def test_nebuko_workflows_dir_still_overrides_defaults(tmp_path, monkeypatch):
    # given: the same fake checkout plus a repo-local override
    defaults_file = tmp_path / "defaults" / "workflows" / "spec-driven-impl.yaml"
    defaults_file.parent.mkdir(parents=True)
    defaults_file.write_text("defaults")
    override = tmp_path / ".nebuko" / "workflows" / "spec-driven-impl.yaml"
    override.parent.mkdir(parents=True)
    override.write_text("repo-local")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "spec-driven-impl.yaml").write_text("root-shadow")

    # when
    candidates = workflow_candidate_paths(str(tmp_path), "spec-driven-impl.yaml")

    # then: the repo-local override wins over defaults
    assert candidates[0] == str(override)
    assert candidates[1] == str(defaults_file)
