"""The test suite never pollutes the real home directory."""

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
