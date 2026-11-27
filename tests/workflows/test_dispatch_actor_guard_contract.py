"""Contract: Guard 3 takes the verified human initiator of a ``repository_dispatch``.

A broker-created dispatch is made with the App installation token, so
``github.actor`` is the bot. The human who started the run is
``client_payload.actor``, accepted only when it equals ``github.repository_owner``
(a GitHub-set value). The patch to ``nebuko-workflow.yaml`` is delivered as
``nebuko-task-logs/workflow-patch.diff`` and applied by a human, so the positive
dispatch cases are ``xfail(strict=True)`` until then; remove the marker by hand
once the patch is on the branch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from workflows.test_issue_comment_condition import evaluate

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"
GUARD_STEP = "Validate runner strategy and Claude Code credentials"
OWNER = "olivier-norture"
BOT = "nebuko-staging[bot]"

# The evaluator raises KeyError on a missing context key, so every key the
# expressions reference is present, set to None when unused.
_CONTEXT_KEYS = (
    "github.event_name",
    "github.event.comment.user.login",
    "github.event.comment.user.type",
    "github.event.issue.user.login",
    "github.event.issue.user.type",
    "github.event.client_payload.actor",
    "github.repository_owner",
    "github.actor",
)


def _guard_env() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == GUARD_STEP:
                return step["env"]
    raise AssertionError(f"step {GUARD_STEP!r} not found in {WORKFLOW_PATH.name}")


def _context(**values: Any) -> dict[str, Any]:
    context: dict[str, Any] = dict.fromkeys(_CONTEXT_KEYS)
    context["github.repository_owner"] = OWNER
    context["github.actor"] = BOT
    context.update({f"github.{key}": value for key, value in values.items()})
    return context


def _comment(login: str = OWNER, user_type: str = "User") -> dict[str, Any]:
    return _context(
        event_name="issue_comment",
        **{"event.comment.user.login": login, "event.comment.user.type": user_type},
    )


def _dispatch(payload_actor: str | None) -> dict[str, Any]:
    return _context(
        event_name="repository_dispatch",
        **{"event.client_payload.actor": payload_actor},
    )


def _manual(actor: str = OWNER) -> dict[str, Any]:
    return _context(event_name="workflow_dispatch", actor=actor)


def _resolve(context: dict[str, Any]) -> tuple[Any, Any]:
    env = _guard_env()
    return (
        evaluate(env["ACTOR_LOGIN"], context),
        evaluate(env["ACTOR_TYPE"], context),
    )


OWNER_DISPATCH_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="needs the nebuko-workflow.yaml actor patch (workflow-patch.diff) applied by a human",
)


@pytest.mark.parametrize(
    ("context", "login", "user_type"),
    [
        pytest.param(_comment(), OWNER, "User", id="comment-unchanged"),
        pytest.param(_manual(), OWNER, "", id="manual-human-unchanged"),
        pytest.param(_dispatch("mallory"), BOT, "", id="dispatch-other-actor-refused"),
        pytest.param(_dispatch(None), BOT, "", id="dispatch-no-payload-actor-refused"),
    ],
)
def test_actor_expression_cases(context, login, user_type):
    assert _resolve(context) == (login, user_type)


@OWNER_DISPATCH_XFAIL
def test_dispatch_by_owner_resolves_to_the_human():
    assert _resolve(_dispatch(OWNER)) == (OWNER, "User")
