"""Scoped phase-override registry (spec) — tests are expected to FAIL against stubs.

The publish-phase test fixture raised ``ModuleNotFoundError`` that escaped the
isolation scope and aborted the run (see issue thread). A scoped
``PhaseOverride`` registry contains test-injected phase failures by
construction: an override is only visible while its ``with`` block is live.
"""

from __future__ import annotations

import pytest

from nebuko.engine.run_loop import PhaseOverride, _resolve_phase_callable


def _identity() -> str:
    return "identity"


def _raises() -> None:
    raise RuntimeError("injected")


def test_override_restored_after_exception():
    """An override that raises is removed from the registry on exit."""
    with pytest.raises(RuntimeError), PhaseOverride("publish", _raises):
        pass
    assert PhaseOverride.active("publish") is None


def test_publish_uses_default_when_no_override_active():
    """With no live override, resolution returns the default callable."""
    assert _resolve_phase_callable("publish", _identity) is _identity


def test_nested_overrides_restore_in_reverse_order():
    """Inner overrides unwind LIFO; the outer override survives the inner exit."""
    with PhaseOverride("publish", _identity):
        assert PhaseOverride.active("publish") is _identity
        with PhaseOverride("publish", lambda: "inner"):
            assert PhaseOverride.active("publish") is not _identity
        assert PhaseOverride.active("publish") is _identity
    assert PhaseOverride.active("publish") is None


def test_raise_publish_scoped_to_override():
    """A publish override that raises is contained: outside the scope the
    default callable resolves and no error leaks to the caller."""
    default = _identity
    with pytest.raises(RuntimeError), PhaseOverride("publish", _raises):
        resolved = _resolve_phase_callable("publish", default)
        assert resolved is not default
        resolved()
    assert _resolve_phase_callable("publish", default) is default
