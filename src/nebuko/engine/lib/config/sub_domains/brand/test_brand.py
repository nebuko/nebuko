"""Tests for the BrandIdentity sub-domain."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import BRAND, BrandIdentity, brand


class TestBrandIdentity:
    """BrandIdentity defaults declare Nebuko as the external identity."""

    def test_brand_defaults(self):
        identity = BrandIdentity()
        assert identity.app_name == "Nebuko"
        assert str(identity.site_url) == "https://github.com/nebuko/nebuko"
        assert str(identity.app_url) == "https://github.com/nebuko/nebuko"

    def test_brand_accessor_returns_singleton(self):
        assert brand() is BRAND
        assert brand().app_name == "Nebuko"

    def test_command_name_defaults_to_nebuko(self):
        # Slash-command prefix source of truth (issue #749).
        assert BrandIdentity().command_name == "nebuko"


class TestLogoMark:
    """``logo_mark()`` is the single source of truth for the comment logo (issue #778)."""

    def test_is_single_line(self):
        assert "\n" not in BrandIdentity().logo_mark()

    def test_fallback_img_is_light_themed_and_app_named(self):
        mark = BrandIdentity().logo_mark()
        assert 'src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg"' in mark
        assert 'alt="Nebuko"' in mark
        assert 'width="16"' in mark
        assert 'height="16"' in mark

    def test_dark_source_is_prefers_color_scheme_dark(self):
        mark = BrandIdentity().logo_mark()
        assert (
            '<source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg"'
            ' media="(prefers-color-scheme: dark)">' in mark
        )

    def test_size_is_field_driven(self):
        mark = BrandIdentity(comment_logo_size=24).logo_mark()
        assert 'width="24"' in mark
        assert 'height="24"' in mark

    def test_alt_text_follows_app_name(self):
        mark = BrandIdentity(app_name="Acme").logo_mark()
        assert 'alt="Acme"' in mark
