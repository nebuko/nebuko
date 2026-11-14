"""Doc contract for #1026 step 4: the GitHub App install is the default path.

The four user-facing entry docs must present the App-install path as the
default narrative and demote ``GH_PAT`` to an explicit PAT fallback. These
tests read the shipped docs as data: they lock the sections, the copy-paste
workflow examples (``id-token: write``, no ``GH_PAT`` wiring) and the trust &
security note that the plan's Doc Contract specifies.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTION = REPO_ROOT / "docs" / "adoption.md"
GETTING_STARTED = REPO_ROOT / "docs" / "getting-started.md"
SUBSCRIPTION_PLANS = REPO_ROOT / "docs" / "subscription-plans.md"
README = REPO_ROOT / "README.md"

APP_INSTALL_URL = "https://github.com/apps/nebuko/installations/new"
TRUST_ANCHOR = "adoption.md#8-trust-and-security"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse markdown line wrapping so phrase assertions are layout-agnostic."""
    return re.sub(r"\s+", " ", text)


def _section(text: str, heading: str) -> str:
    """Return the body of the ``## <heading>`` section, up to the next ``## ``."""
    match = re.search(rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, f"missing section: ## {heading}"
    return match.group(1)


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, re.S)


def _workflow_blocks(text: str) -> list[str]:
    return [block for block in _yaml_blocks(text) if "nebuko-workflow.yaml" in block]


def _pat_mentions(text: str) -> list[str]:
    return [line for line in text.splitlines() if "GH_PAT" in line]


def test_given_the_adoption_doc_when_read_then_the_app_install_is_the_default_path():
    text = _read(ADOPTION)

    assert APP_INSTALL_URL in text
    assert "PAT mode (alpha default)" not in text
    assert "Optional GitHub App (coming)" not in text
    assert "GitHub App installation by default" in _flat(text)


def test_given_the_adoption_prerequisites_when_read_then_no_pat_is_required():
    prerequisites = _section(_read(ADOPTION), "2. Prerequisites")

    assert APP_INSTALL_URL in prerequisites
    assert "GH_PAT" not in prerequisites


def test_given_the_adoption_workflow_example_when_rendered_then_it_wires_no_pat_secret():
    blocks = _workflow_blocks(_read(ADOPTION))

    assert blocks, "adoption.md must keep a copy-paste workflow example"
    for block in blocks:
        assert "GH_PAT" not in block


def test_given_the_adoption_workflow_example_when_rendered_then_id_token_write_is_granted():
    blocks = [block for block in _workflow_blocks(_read(ADOPTION)) if "permissions:" in block]

    assert blocks
    for block in blocks:
        assert "id-token: write" in block


def test_given_the_adoption_doc_when_read_then_pat_is_documented_as_a_fallback_subsection():
    text = _read(ADOPTION)

    assert "## 4. Authentication — GitHub App (default)" in text
    authentication = _section(text, "4. Authentication — GitHub App (default)")
    assert "OIDC broker" in _flat(authentication)
    assert "installation token" in _flat(authentication)

    fallback = authentication.split("### PAT fallback", 1)
    assert len(fallback) == 2, "PAT mode must survive as a '### PAT fallback' subsection"
    assert "fine-grained" in fallback[1]


def test_given_the_adoption_doc_when_read_then_the_trust_section_lists_permissions_and_lifecycle():
    trust = _section(_read(ADOPTION), "8. Trust and security")

    for permission in ("Contents", "Pull requests", "Issues"):
        assert permission in trust
    assert "private key" in trust
    assert "NEBUKO_BROKER_URL" in trust
    assert "Suspend" in trust
    assert "Uninstall" in trust


def test_given_the_adoption_doc_when_read_then_the_broker_variable_is_documented():
    text = _read(ADOPTION)

    assert "NEBUKO_BROKER_URL" in text
    assert "repository variable" in text


def test_given_the_getting_started_doc_when_read_then_the_secret_table_lists_no_pat():
    text = _read(GETTING_STARTED)

    assert APP_INSTALL_URL in text
    assert not [line for line in text.splitlines() if line.startswith("| `GH_PAT`")]
    assert "OIDC broker" in _flat(text)


def test_given_the_getting_started_doc_when_read_then_it_links_the_app_trust_note():
    text = _read(GETTING_STARTED)

    assert TRUST_ANCHOR in text


def test_given_the_readme_quickstart_when_rendered_then_no_pat_secret_is_wired():
    blocks = _workflow_blocks(_read(README))

    assert blocks, "README quickstart must keep its workflow example"
    for block in blocks:
        assert "GH_PAT" not in block
        assert "id-token: write" in block


def test_given_the_readme_when_read_then_the_safety_section_describes_app_mode():
    text = _read(README)
    safety = _section(text, "Safety")

    assert APP_INSTALL_URL in text
    assert "short-lived" in safety
    assert TRUST_ANCHOR in safety

def test_given_the_readme_status_when_read_then_the_app_install_is_the_default():
    text = _read(README)

    assert "GitHub App install is the default path" in _flat(text)
    assert "PAT mode only" not in text


def test_given_the_subscription_plans_doc_when_read_then_the_secret_example_wires_no_pat():
    blocks = _workflow_blocks(_read(SUBSCRIPTION_PLANS))

    assert blocks
    for block in blocks:
        assert "COMMANDCODE_API_KEY" in block
        assert "GH_PAT" not in block


def test_given_the_entry_docs_when_scanned_then_every_pat_mention_is_labelled_fallback():
    adoption = _read(ADOPTION)
    head, _, fallback = adoption.partition("### PAT fallback")

    for line in _pat_mentions(head):
        assert "fallback" in line.lower(), f"unlabelled PAT mention: {line!r}"

    for path in (GETTING_STARTED, README, SUBSCRIPTION_PLANS):
        for line in _pat_mentions(_read(path)):
            assert "fallback" in line.lower(), f"{path.name}: unlabelled PAT mention: {line!r}"
