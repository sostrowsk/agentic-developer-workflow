"""RED tests for Aufgabe A — the test suite must never pollute the real home.

Derived from .adw/spec.md (AC 1–3) and .adw/plan.md (A1–A3). The bug: ``adw run``
auto-registers its target repo in ``~/.adw/repos.json`` (adw/gui/registry.py,
fail-open), and the E2E dry-run tests use throwaway ``tmp_path`` repos, so their
entries leak into the developer's REAL registry. The fix makes the registry path
isolable and applies that isolation to every test that triggers ``adw run`` /
auto-registration (a shared, autouse conftest fixture). The runtime behaviour of
``adw run`` (fail-open write to ``~/.adw/repos.json`` in the pinned format) is
unchanged.

These tests are RED until the isolation exists: without it, the dry run below
writes the ``tmp_path`` repo into the file at the real resolved home path, so the
"real file unchanged" assertion fails. Each test snapshots and restores that file
in a ``finally`` so a RED run never leaves lasting pollution behind.
"""

import os
from pathlib import Path

from typer.testing import CliRunner

from adw.cli import app
from adw.gui.registry import load_registry

# Captured at import time — before any per-test HOME monkeypatching — so it is the
# genuinely real registry path regardless of the (not contractual) isolation
# mechanism the fix chooses.
_REAL_REGISTRY = Path(os.path.expanduser("~")) / ".adw" / "repos.json"

cli = CliRunner()


def _snapshot():
    return _REAL_REGISTRY.read_bytes() if _REAL_REGISTRY.exists() else None


def _restore(before):
    """Undo any pollution — but ONLY if the real file actually changed. On the
    green path (isolation active) nothing changed, so we never write to the real
    home at all (which may be read-only)."""
    if _snapshot() == before:
        return
    if before is None:
        if _REAL_REGISTRY.exists():
            _REAL_REGISTRY.unlink()
    else:
        _REAL_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        _REAL_REGISTRY.write_bytes(before)


def test_dry_run_leaves_real_home_registry_bit_identical(target_repo):
    """AC 1: a run that triggers auto-registration with a tmp_path repo leaves the
    real ~/.adw/repos.json untouched — absent stays absent, present stays
    bit-identical, and the tmp_path never appears there."""
    before = _snapshot()
    try:
        result = cli.invoke(
            app,
            ["run", "--repo", str(target_repo), "--issue", "Iso", "--dry-run", "--no-approval"],
        )
        assert result.exit_code == 0, result.output
        after = _snapshot()
        assert after == before  # real registry file unchanged by the test run
        if after is not None:
            assert str(target_repo.resolve()) not in after.decode("utf-8", "replace")
    finally:
        _restore(before)


def test_isolation_applies_to_auto_registration_outside_registry_own_tests(target_repo):
    """AC 2/3: the isolation grips for a plain ``adw run`` entrypoint (not only the
    registry's own tests) — registration still happens (behaviour unchanged), but
    into the isolated registry, so the real home stays clean."""
    before = _snapshot()
    try:
        result = cli.invoke(
            app,
            ["run", "--repo", str(target_repo), "--issue", "Iso", "--dry-run", "--no-approval"],
        )
        assert result.exit_code == 0, result.output
        # Registration still works — the resolved repo is in the (isolated) registry.
        reg = load_registry()
        assert str(target_repo.resolve()) in [e.path for e in reg.repos]
        # ... yet the real home registry did not change.
        assert _snapshot() == before
    finally:
        _restore(before)
