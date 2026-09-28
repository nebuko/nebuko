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
