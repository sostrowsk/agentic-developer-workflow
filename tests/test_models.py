"""Model freshness check: ADW refuses to run on models older than the newest."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from adw import agents, codex, models

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
PINNED_CLAUDE = {"opus": agents.OPUS, "fable": agents.FABLE, "sonnet": agents.SONNET}
NEWER_OPUS = dict(PINNED_CLAUDE, opus="claude-opus-6")


def _catalog(*slugs, upgrade=None):
    entries = [{"slug": s, "visibility": "list", "upgrade": None} for s in slugs]
    if upgrade:
        for e in entries:
            if e["slug"] == upgrade[0]:
                e["upgrade"] = {"model": upgrade[1]}
    return {"models": entries}


def test_latest_codex_model_picks_newest_version_of_the_pinned_tier():
    catalog = _catalog("gpt-6.1-sol", "gpt-6-astra", "gpt-6.2-astra", "gpt-7-luna")
    assert models.latest_codex_model(catalog, "gpt-6-astra") == "gpt-6.2-astra"


def test_latest_codex_model_keeps_pinned_when_nothing_newer():
    catalog = _catalog("gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol")
    assert models.latest_codex_model(catalog, "gpt-6-astra") == "gpt-6-astra"


def test_latest_codex_model_follows_announced_upgrade():
    catalog = _catalog("gpt-6-astra", "gpt-6.1-sol", upgrade=("gpt-6-astra", "gpt-6.1-sol"))
    assert models.latest_codex_model(catalog, "gpt-6-astra") == "gpt-6.1-sol"


def test_latest_codex_model_ignores_hidden_models():
    catalog = _catalog("gpt-6-astra")
    catalog["models"].append({"slug": "gpt-9-astra", "visibility": "hide", "upgrade": None})
    assert models.latest_codex_model(catalog, "gpt-6-astra") == "gpt-6-astra"


def test_find_outdated_names_role_pinned_and_latest():
    latest = dict(PINNED_CLAUDE, opus="claude-opus-6", codex=codex.CODEX_MODEL)
    outdated = models.find_outdated(latest)
    assert [(o.role, o.pinned, o.latest) for o in outdated] == [
        ("opus", agents.OPUS, "claude-opus-6")
    ]


def test_find_outdated_skips_roles_that_could_not_be_checked():
    assert models.find_outdated({"opus": agents.OPUS}) == []


def test_fresh_cache_is_reused_without_probing(tmp_path):
    cache = tmp_path / "check.json"
    cached = dict(PINNED_CLAUDE, opus="claude-opus-6")
    cache.write_text(json.dumps({"checked_at": (NOW - timedelta(hours=23)).isoformat(),
                                 "pinned": models.pinned_models(), "latest": cached}))

    def no_probe(alias):
        raise AssertionError("fresh cache must not probe")

    latest, warnings = models.latest_models(cache, NOW, no_probe, lambda: None)
    assert latest == cached
    assert warnings == []


def test_stale_cache_is_refreshed_and_written(tmp_path):
    cache = tmp_path / "check.json"
    cache.write_text(json.dumps({"checked_at": (NOW - timedelta(hours=25)).isoformat(),
                                 "latest": {"opus": "old"}}))
    catalog = _catalog(codex.CODEX_MODEL)
    latest, warnings = models.latest_models(cache, NOW, PINNED_CLAUDE.get, lambda: catalog)
    assert latest == dict(PINNED_CLAUDE, codex=codex.CODEX_MODEL)
    assert warnings == []
    assert json.loads(cache.read_text())["latest"] == latest


def test_probe_failure_warns_and_is_not_cached(tmp_path):
    cache = tmp_path / "check.json"

    def broken(alias):
        raise RuntimeError("plan limit")

    latest, warnings = models.latest_models(cache, NOW, broken, lambda: None)
    assert latest == {}
    assert any("plan limit" in w for w in warnings)
    assert any("Codex" in w for w in warnings)
    assert not cache.exists()


def test_outdated_message_points_to_where_adw_is_updated():
    msg = models.outdated_message([models.Outdated("codex", "gpt-6-astra", "gpt-6.2-astra")])
    assert "gpt-6-astra → gpt-6.2-astra" in msg
    assert "adw/codex.py" in msg


@pytest.fixture
def newer_opus(monkeypatch):
    monkeypatch.setattr(models, "resolve_claude_alias", NEWER_OPUS.__getitem__)


# --- CLI preflight ---------------------------------------------------------------


def _cli():
    from typer.testing import CliRunner

    from adw.cli import app

    return CliRunner(), app


def _no_execution(monkeypatch):
    import adw.cli as cli_mod

    def boom(ctx):
        raise AssertionError("_execute must not run with outdated models")

    monkeypatch.setattr(cli_mod, "_execute", boom)


def test_run_refuses_when_newer_models_exist(target_repo, newer_opus, monkeypatch):
    from adw.state import RunState, StateNotFoundError

    _no_execution(monkeypatch)
    runner, app = _cli()
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo"])
    assert res.exit_code == 1, res.output
    assert f"{agents.OPUS} → claude-opus-6" in res.output
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)


def test_dry_run_skips_the_model_check(target_repo, monkeypatch):
    def no_probe(alias):
        raise AssertionError("dry run must stay at 0 tokens")

    monkeypatch.setattr(models, "resolve_claude_alias", no_probe)
    runner, app = _cli()
    res = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"]
    )
    assert res.exit_code == 0, res.output


def test_resume_refuses_when_newer_models_exist(target_repo, monkeypatch):
    from adw.state import RunState

    runner, app = _cli()
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    assert res.exit_code == 2, res.output
    state = RunState.find_latest(target_repo)
    state.dry_run = False
    state.save(target_repo)
    monkeypatch.setattr(models, "resolve_claude_alias", NEWER_OPUS.__getitem__)
    _no_execution(monkeypatch)
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1, res.output
    assert "claude-opus-6" in res.output
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


def test_probe_failure_does_not_block_a_run(target_repo, monkeypatch, capsys):
    def broken(alias):
        raise RuntimeError("offline")

    monkeypatch.setattr(models, "resolve_claude_alias", broken)
    monkeypatch.setattr(models, "load_codex_catalog", lambda: None)
    import adw.cli as cli_mod

    reached = []
    monkeypatch.setattr(cli_mod, "_execute", lambda ctx: reached.append(True))
    runner, app = _cli()
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo"])
    assert res.exit_code == 0, res.output
    assert reached
    assert "offline" in res.output


# --- Codex review round 1 regressions -----------------------------------------------


def test_cache_from_older_pins_is_not_reused(tmp_path):
    """After ADW was updated to newer pins, a cache written under the OLD pins must
    not report the old models as 'latest' and block the updated installation."""
    cache = tmp_path / "check.json"
    old_pins = dict(models.pinned_models(), opus="claude-opus-4-8")
    cache.write_text(json.dumps({"checked_at": NOW.isoformat(), "pinned": old_pins,
                                 "latest": old_pins}))
    catalog = _catalog(codex.CODEX_MODEL)
    latest, _ = models.latest_models(cache, NOW, PINNED_CLAUDE.get, lambda: catalog)
    assert latest["opus"] == agents.OPUS
    assert models.find_outdated(latest) == []


def test_stale_codex_catalog_is_unchecked_with_warning(tmp_path):
    catalog = _catalog("gpt-6-astra")
    catalog["fetched_at"] = (NOW - timedelta(days=8)).isoformat()
    latest, warnings = models.latest_models(
        tmp_path / "check.json", NOW, PINNED_CLAUDE.get, lambda: catalog
    )
    assert "codex" not in latest
    assert any("veraltet" in w for w in warnings)


def test_cache_write_failure_only_warns(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    cache = blocker / "check.json"  # parent is a file -> cannot be written
    catalog = _catalog(codex.CODEX_MODEL)
    latest, warnings = models.latest_models(cache, NOW, PINNED_CLAUDE.get, lambda: catalog)
    assert latest == dict(PINNED_CLAUDE, codex=codex.CODEX_MODEL)
    assert any("Cache" in w for w in warnings)


@pytest.mark.parametrize("catalog", [[], {"models": None}, {"models": [None]}, "x"])
def test_malformed_codex_catalog_only_warns(tmp_path, catalog):
    latest, warnings = models.latest_models(
        tmp_path / "check.json", NOW, PINNED_CLAUDE.get, lambda: catalog
    )
    assert "codex" not in latest
    assert any("Katalog" in w for w in warnings)
