"""Author identity value object (issue #648).

Gate-release authority must be anchored to the **GitHub event author** — the
identity GitHub attests to out-of-band in the webhook payload — never to
content found in the comment body. Body markers are forgeable (an agent posts
to the same comment stream as a human), so the gate path derives trust from
this value object instead.

``AuthorIdentity`` captures only what the identity check needs; the concrete
login/type/association come from CI environment variables (see the CI wiring in
``.github/workflows/nebuko-workflow.yaml``), and the machine-comment flag comes
from scanning the body for known agent tokens.
"""

from typing import Tuple

from pydantic import BaseModel, ConfigDict

from ...features.parse_trigger.parse_trigger import is_machine_comment

__all__ = [
    "AuthorIdentity",
    "build_author_identity",
    "BOT_LOGIN_DENYLIST",
    "BOT_LOGIN_SUFFIXES",
]

#: Login suffixes that always denote a GitHub App / bot account.
BOT_LOGIN_SUFFIXES: Tuple[str, ...] = ("[bot]",)

#: Logins known to be machine accounts. GitHub only types GitHub App accounts
#: as ``Bot``; machine *user* accounts (e.g. ``k8s-ci-robot``) are typed
#: ``User`` (GitHub community discussion #65546), so a ``type`` check alone is
#: insufficient and an explicit deny-list is kept alongside it.
BOT_LOGIN_DENYLIST: Tuple[str, ...] = (
    "github-actions",
    "github-actions[bot]",
    "dependabot",
    "dependabot[bot]",
    "renovate",
    "renovate[bot]",
    "nebuko",
    "nebuko-bot",
)

#: Associations allowed to release a gate (Policy A, issue #648). Gate commands
#: are privileged **regardless of ``CALLER_AUTH``**: they are always restricted
#: to repository owner/member/collaborator authors.
DEFAULT_GATE_ALLOWLIST: Tuple[str, ...] = ("OWNER", "MEMBER", "COLLABORATOR")


class AuthorIdentity(BaseModel):
    """Verified identity of the GitHub event author behind a trigger.

    Frozen: the routing layer may pass it around but never mutate it.

    Attributes:
        login: GitHub login of the author (empty when unknown).
        is_bot: True for bot accounts — either GitHub-typed ``Bot`` or a login
            matched by :data:`BOT_LOGIN_DENYLIST` / :data:`BOT_LOGIN_SUFFIXES`.
        association: ``author_association`` from the event (``OWNER``,
            ``MEMBER``, ``COLLABORATOR``, ``CONTRIBUTOR``, ``NONE``, ...).
        is_machine_comment: True when the body carries any machine token, so a
            human-account comment echoing agent output cannot carry a decision.
    """

    model_config = ConfigDict(frozen=True)

    login: str
    is_bot: bool = False
    association: str = ""
    is_machine_comment: bool = False

    # @stub-protected: signature defined by architect — do not modify
    def may_decide_gate(
        self, allowlist: Tuple[str, ...] = DEFAULT_GATE_ALLOWLIST
    ) -> bool:
        """True iff this author may release a human gate.

        An author may decide a gate only when they are not a bot, their comment
        does not carry machine tokens, and their repository association is in
        ``allowlist`` (Policy A, issue #648).
        """
        if self.is_bot or self.is_machine_comment:
            return False
        return self.association in allowlist


def _is_bot_login(login: str) -> bool:
    """True when ``login`` is a known bot/machine account."""
    if not login:
        return False
    if login in BOT_LOGIN_DENYLIST:
        return True
    return login.endswith(BOT_LOGIN_SUFFIXES)


# @stub-protected: signature defined by architect — do not modify
def build_author_identity(
    login: str,
    user_type: str,
    association: str,
    trigger_body: str,
) -> AuthorIdentity:
    """Assemble an :class:`AuthorIdentity` from CI event fields.

    The login/type/association come from the GitHub event (CI environment),
    never from body markers; only ``is_machine_comment`` reads the body.
    """
    normalized_login = (login or "").strip()
    is_bot = (user_type or "").strip() == "Bot" or _is_bot_login(normalized_login)
    return AuthorIdentity(
        login=normalized_login,
        is_bot=is_bot,
        association=(association or "").strip(),
        is_machine_comment=is_machine_comment(trigger_body or ""),
    )
