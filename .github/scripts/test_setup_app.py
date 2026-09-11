"""Tests for the GitHub App setup helper (setup_app.py, issue #303)."""

import io
import json
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from setup_app import (
    APP_SLUG,
    SetupError,
    build_manifest,
    build_parser,
    consumer_secrets_yaml,
    lookup_app_and_installations,
    main,
    manifest_url,
    secrets_block,
)


# ── build_manifest / manifest_url (pure) ────────────────────────────────


class TestBuildManifest:
    def test_defaults(self):
        manifest = build_manifest()
        assert manifest["slug"] == "git-flow-ai"
        assert manifest["name"] == "git-flow-ai"
        assert manifest["default_permissions"]["contents"] == "write"
        assert manifest["default_permissions"]["issues"] == "write"
        assert manifest["default_permissions"]["pull_requests"] == "write"
        assert "issue_comment" in manifest["events"]
        assert manifest["public"] is True

    def test_custom_values(self):
        manifest = build_manifest(slug="my-app", permissions={"contents": "read"},
                                  events=["issues"])
        assert manifest["slug"] == "my-app"
        assert manifest["default_permissions"] == {"contents": "read"}
        assert manifest["events"] == ["issues"]


class TestManifestUrl:
    def test_encodes_payload(self):
        url = manifest_url(build_manifest())
        assert url.startswith("https://github.com/apps/new?manifest=")
        payload = json.loads(
            urllib.request.unquote(url.split("manifest=", 1)[1])
        )
        assert payload["slug"] == "git-flow-ai"


# ── secrets rendering (pure) ────────────────────────────────────────────


class TestSecretsBlock:
    def test_renders_three_secrets(self):
        out = secrets_block("git-flow-ai", 12345, [67890, 11111])
        assert "GH_APP_ID: 12345" in out
        assert "GH_APP_INSTALLATION_ID: 67890" in out  # primary = first
        assert "GH_APP_PRIVATE_KEY:" in out

    def test_requires_installation(self):
        with pytest.raises(SetupError):
            secrets_block("git-flow-ai", 1, [])


class TestConsumerSecretsYaml:
    def test_renders_yaml_mapping(self):
        out = consumer_secrets_yaml("git-flow-ai", 12345, [67890])
        assert "GH_APP_ID: ${{ secrets.GH_APP_ID }}" in out
        assert "GH_APP_PRIVATE_KEY: ${{ secrets.GH_APP_PRIVATE_KEY }}" in out
        assert "GH_APP_INSTALLATION_ID: ${{ secrets.GH_APP_INSTALLATION_ID }}" in out


# ── CLI subcommands (pure ones) ─────────────────────────────────────────


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


class TestCli:
    def test_manifest_url_command(self):
        code, out = _run(["manifest-url"])
        assert code == 0
        assert out.startswith("https://github.com/apps/new?manifest=")

    def test_secrets_command(self):
        code, out = _run(["secrets", "--app-id", "1", "--installation-id", "2,3"])
        assert code == 0
        assert "GH_APP_ID: 1" in out
        assert "GH_APP_INSTALLATION_ID: 2" in out

    def test_print_secrets_command(self):
        code, out = _run(["print-secrets", "--app-id", "1", "--installation-id", "2"])
        assert code == 0
        assert "${{ secrets.GH_APP_ID }}" in out

    def test_parser_requires_command(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


# ── lookup (I/O boundary, mocked urlopen) ───────────────────────────────


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


APP_RECORD = {"id": 12345, "slug": "git-flow-ai", "name": "git-flow-ai"}
INSTALLATIONS = [{"id": 67890, "account": {"login": "octo"}}]


class TestLookup:
    def _urlopen(self, payloads):
        return patch("setup_app.urllib.request.urlopen",
                     side_effect=lambda *a, **k: _fake_urlopen_factory(payloads)(a[0]))

    def test_returns_app_and_installations(self):
        with self._urlopen({"/app": APP_RECORD, "/app/installations": INSTALLATIONS}):
            app, installations = lookup_app_and_installations("git-flow-ai", "token")
        assert app["id"] == 12345
        assert [i["id"] for i in installations] == [67890]

    def test_slug_mismatch_raises(self):
        with self._urlopen({"/app": {**APP_RECORD, "slug": "other"},
                            "/app/installations": INSTALLATIONS}):
            with pytest.raises(SetupError, match="expected 'git-flow-ai'"):
                lookup_app_and_installations("git-flow-ai", "token")

    def test_no_installations_still_returns(self):
        with self._urlopen({"/app": APP_RECORD, "/app/installations": []}):
            app, installations = lookup_app_and_installations("git-flow-ai", "token")
        assert installations == []

    def test_lookup_command_prints_guidance(self):
        with self._urlopen({"/app": APP_RECORD, "/app/installations": []}):
            code, out = _run(["lookup", "--token", "t"])
        assert code == 0
        assert "No installations yet" in out

    def test_lookup_command_prints_secrets(self):
        with self._urlopen({"/app": APP_RECORD, "/app/installations": INSTALLATIONS}):
            code, out = _run(["lookup", "--token", "t"])
        assert code == 0
        assert "GH_APP_ID: 12345" in out
        assert "GH_APP_INSTALLATION_ID: 67890" in out


def _fake_urlopen_factory(payload_by_path):
    """Return a fake urlopen routing on the request URL path."""

    def factory(request, *args, **kwargs):
        for path, payload in payload_by_path.items():
            if request.full_url.endswith(path):
                return _FakeResponse(io.BytesIO(json.dumps(payload).encode()))
        raise urllib.error.URLError(f"unexpected URL {request.full_url}")

    return factory