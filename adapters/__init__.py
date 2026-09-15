"""adapters — platform adapters for the engine driver (Stage A of #585).

The engine depends only on the `PlatformAdapter` protocol; each concrete
adapter (GitHub, Local) confines all platform I/O behind it.
"""

from .base import PlatformAdapter

__all__ = ["PlatformAdapter"]