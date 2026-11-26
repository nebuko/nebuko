"""Lane stickiness, personal manifest and the personal secret check (issue #1479)."""

from nebuko.engine.harness.credentials import (
    USER_REPO_LANE,
    check_personal_secrets,
    decide_lane,
)
from nebuko.engine.models import PersonalManifest

#: Engine marker literals must never reach a refusal or notice (the #1459 lesson).
MARKER_LITERALS = ("<!--", "-->", "nebuko:refused", "bot-trigger", "bot-human-action")


def _clean(text):
    return not any(token in text for token in MARKER_LITERALS)


class TestDecideLane:
    def test_flagless_comment_on_personal_thread_is_refused_for_the_owner_too(self):
        owner = decide_lane(USER_REPO_LANE, "alice", None, "alice")
        other = decide_lane(USER_REPO_LANE, "alice", None, "bob")
        assert owner.action == other.action == "refuse"
        assert "--personal" in owner.message and "--project" in owner.message
        assert _clean(owner.message)

    def test_personal_by_the_owner_continues(self):
        assert decide_lane(USER_REPO_LANE, "alice", "personal", "alice").action == "allow"

    def test_personal_by_another_person_is_a_takeover_with_notice(self):
        decision = decide_lane(USER_REPO_LANE, "alice", "personal", "bob")
        assert decision.action == "takeover"
        assert decision.notice == "credential owner changed from alice to bob"
        assert _clean(decision.notice)

    def test_project_flag_moves_the_run_back(self):
        assert decide_lane(USER_REPO_LANE, "alice", "project", "bob").action == "move_to_project"

    def test_personal_flag_on_project_run_switches(self):
        assert decide_lane(None, None, "personal", "alice").action == "switch_to_personal"

    def test_flagless_project_comment_is_allowed(self):
        assert decide_lane(None, None, None, "alice").action == "allow"


class TestPersonalSecrets:
    def test_missing_secret_names_config_and_secret(self):
        message = check_personal_secrets("agent-config-x.yaml", ["TOKEN_X"], {})
        assert message == (
            "config agent-config-x.yaml needs secret TOKEN_X, not available in the personal lane"
        )

    def test_present_secret_passes(self):
        assert check_personal_secrets("c.yaml", ["TOKEN_X"], {"TOKEN_X": "t"}) is None


class TestPersonalManifest:
    def test_only_agent_config_is_read_and_other_keys_are_reported(self, tmp_path):
        path = tmp_path / "nebuko.yaml"
        path.write_text(
            "agent_config: agent-config-x.yaml\nbase_branch: dev\ntest_command: make\n",
            encoding="utf-8",
        )
        manifest, ignored = PersonalManifest.load(path)
        assert manifest.agent_config == "agent-config-x.yaml"
        assert ignored == ["base_branch", "test_command"]

    def test_absent_file_is_empty(self, tmp_path):
        manifest, ignored = PersonalManifest.load(tmp_path / "missing.yaml")
        assert manifest.agent_config is None
        assert ignored == []


def test_project_side_skips_a_personal_command_without_a_reply(tmp_path, monkeypatch):
    from nebuko.engine.lib.resolve_config.resolve_config import resolve_config

    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    result = resolve_config(
        trigger_body="/nebuko approve gate --personal",
        prompt_base="",
        workflows_dir=str(tmp_path),
        is_bot=False,
    )
    assert result["SKIP_AGENT"] == "true"
    assert result.get("COMMAND_ERROR") != "true"


class _RecordingAdapter:
    def __init__(self):
        self.bodies = []

    def post(self, body):
        self.bodies.append(body)
        return body


class TestRunNoticesReachTheThread:
    """The lane and config notices are posted, not just carried (issue #1479)."""

    def _engine(self, tmp_path, adapter):
        from nebuko.engine.run_loop import Engine

        return Engine(adapter=adapter, repo_root=tmp_path, env={})

    def test_takeover_notice_is_posted_as_the_first_comment(self, tmp_path, monkeypatch):
        from nebuko.engine import run_loop

        monkeypatch.setattr(run_loop, "_is_bot_trigger", lambda trigger: False)
        adapter = _RecordingAdapter()
        engine = self._engine(tmp_path, adapter)
        decision = decide_lane(USER_REPO_LANE, "alice", "personal", "bob")
        posted = engine._post_run_notices({"LANE_NOTICE": decision.notice}, object())

        assert adapter.bodies == [posted]
        assert "- credential owner changed from alice to bob" in posted
        assert _clean(posted.replace("<!-- bot-workflow-terminal -->", ""))

    def test_config_line_names_layer_and_file_for_a_human_run(self, tmp_path, monkeypatch):
        from nebuko.engine import run_loop
        from nebuko.engine.lib.resolve_config.features.build_result.build_result import (
            _agent_config_notice,
        )

        monkeypatch.setattr(run_loop, "_is_bot_trigger", lambda trigger: False)
        adapter = _RecordingAdapter()
        engine = self._engine(tmp_path, adapter)
        notice = _agent_config_notice(
            "fast", "personal", "/checkout/personal/.nebuko/agents/fast.yaml"
        )
        posted = engine._post_run_notices({"AGENT_CONFIG_NOTICE": notice}, object())

        assert "agent config 'fast' from the personal layer (file fast.yaml)" in posted

    def test_config_line_is_not_posted_for_a_bot_continuation(self, tmp_path, monkeypatch):
        from nebuko.engine import run_loop

        monkeypatch.setattr(run_loop, "_is_bot_trigger", lambda trigger: True)
        adapter = _RecordingAdapter()
        engine = self._engine(tmp_path, adapter)
        posted = engine._post_run_notices(
            {"AGENT_CONFIG_NOTICE": "agent config 'x' from the project layer (file x.yaml)"},
            object(),
        )

        assert posted is None
        assert adapter.bodies == []
