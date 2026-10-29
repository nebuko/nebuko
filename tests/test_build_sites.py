"""Tests for scripts/build_sites.py (per-site maintenance page generation)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_sites import SITES, SiteBuilder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
MAINTENANCE_CONFIG = REPO_ROOT / "maintenance.yaml"


def make_builder(tmp_path: Path, config_text: str | None) -> SiteBuilder:
    config_path = tmp_path / "maintenance.yaml"
    if config_text is not None:
        config_path.write_text(config_text, encoding="utf-8")
    return SiteBuilder(config_path, tmp_path)


def test_all_sites_enabled_by_repo_config(tmp_path: Path) -> None:
    builder = SiteBuilder(MAINTENANCE_CONFIG, tmp_path)
    outputs = builder.build()
    assert sorted(p.parent.name for p in outputs) == sorted(SITES)
    for site in SITES:
        page = tmp_path / site / "index.html"
        logo = tmp_path / site / "logo.svg"
        assert page.exists()
        assert logo.exists()
        assert "Nebuko" in page.read_text(encoding="utf-8")


def test_flag_off_with_no_existing_content_writes_placeholder(tmp_path: Path) -> None:
    builder = make_builder(tmp_path, "maintenance:\n  site: false\n  docs: false\n  blog: false\n")
    outputs = builder.build()
    assert len(outputs) == 3
    page = tmp_path / "site" / "index.html"
    assert page.exists()
    assert "Coming soon" in page.read_text(encoding="utf-8")


def test_flag_off_keeps_existing_content_untouched(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("real content", encoding="utf-8")
    builder = make_builder(tmp_path, "maintenance:\n  site: false\n  docs: false\n  blog: false\n")
    outputs = builder.build()
    assert tmp_path / "site" / "index.html" not in outputs  # site untouched
    assert (tmp_path / "site" / "index.html").read_text(encoding="utf-8") == "real content"


def test_missing_keys_default_to_true(tmp_path: Path) -> None:
    builder = make_builder(tmp_path, "maintenance:\n  site: false\n")
    flags = builder.load_config()
    assert flags == {"site": False, "docs": True, "blog": True}


def test_missing_config_file_defaults_to_true(tmp_path: Path) -> None:
    builder = make_builder(tmp_path, None)
    assert builder.load_config() == dict.fromkeys(SITES, True)


def test_enabled_site_replaces_previous_maintenance_page(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("stale", encoding="utf-8")
    builder = make_builder(tmp_path, "maintenance:\n  site: true\n  docs: false\n  blog: false\n")
    builder.build()
    assert (tmp_path / "site" / "index.html").read_text(encoding="utf-8") != "stale"
    assert (tmp_path / "site" / "logo.svg").exists()


@pytest.mark.parametrize("site", SITES)
def test_repo_config_has_entry_for_every_site(site: str) -> None:
    builder = SiteBuilder(MAINTENANCE_CONFIG, Path("/nonexistent"))
    assert site in builder.load_config()
