"""Brand identity sub-domain."""

from .brand import BrandIdentity

BRAND = BrandIdentity()


def brand() -> BrandIdentity:
    """Return the process-wide BrandIdentity singleton."""
    return BRAND


__all__ = ["BrandIdentity", "brand", "BRAND"]
