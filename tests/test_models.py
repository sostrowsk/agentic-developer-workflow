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
    return {"models": entries, "fetched_at": NOW.isoformat()}


def _seed_cache(path, checked_at, roles, pinned=None):
    """Per-role cache entries: {role: model id, or None for a failed probe}."""
    path.write_text(json.dumps({
        "pinned": pinned or models.pinned_models(),
        "roles": {
            role: {"model": model, "checked_at": checked_at, "error": None if model else "x"}
            for role, model in roles.items()
        },
    }))


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
    cached = dict(PINNED_CLAUDE, opus="claude-opus-6", codex=codex.CODEX_MODEL)
    _seed_cache(cache, (NOW - timedelta(hours=23)).isoformat(), cached)

    def no_probe(alias):
        raise AssertionError("fresh cache must not probe")

    latest, warnings = models.latest_models(cache, NOW, no_probe, lambda: None)
    assert latest == cached
    assert warnings == []


def test_stale_cache_is_refreshed_and_written(tmp_path):
    cache = tmp_path / "check.json"
    _seed_cache(cache, (NOW - timedelta(hours=25)).isoformat(), {"opus": "claude-opus-1"})
    catalog = _catalog(codex.CODEX_MODEL)
    latest, warnings = models.latest_models(cache, NOW, PINNED_CLAUDE.get, lambda: catalog)
    assert latest == dict(PINNED_CLAUDE, codex=codex.CODEX_MODEL)
    assert warnings == []
    roles = json.loads(cache.read_text())["roles"]
    assert {role: entry["model"] for role, entry in roles.items()} == latest


def test_probe_failure_warns(tmp_path):
    cache = tmp_path / "check.json"

    def broken(alias):
        raise RuntimeError("plan limit")

    latest, warnings = models.latest_models(cache, NOW, broken, lambda: None)
    assert latest == {}
    assert any("plan limit" in w for w in warnings)
    assert any("Codex" in w for w in warnings)


# --- Review findings 0.29.1 -------------------------------------------------------------


@pytest.mark.parametrize(
    "resolved, outdated",
    [
        ("claude-opus-4-8", False),            # older than the pin: never "newer"
        (f"{agents.OPUS}-20260915", False),    # dated snapshot of the same model
        (f"{agents.OPUS}[1m]", False),         # suffixed variant of the same model
        ("claude-opus-5-6", True),
        ("claude-opus-6", True),
        ("claude-sonnet-9", False),            # another line never replaces the pin
        ("something-else", False),             # unparsable: not a proven newer model
    ],
)
def test_find_outdated_only_flags_a_proven_newer_claude_model(resolved, outdated):
    latest = dict(PINNED_CLAUDE, opus=resolved)
    assert bool(models.find_outdated(latest)) is outdated


def test_successful_roles_are_cached_even_when_another_probe_fails(tmp_path):
    cache = tmp_path / "check.json"
    calls = []

    def flaky(alias):
        calls.append(alias)
        if alias == "fable":
            raise RuntimeError("rate limited")
        return PINNED_CLAUDE[alias]

    catalog = _catalog(codex.CODEX_MODEL)
    models.latest_models(cache, NOW, flaky, lambda: catalog)
    assert calls == ["opus", "fable", "sonnet"]
    # Within the failure back-off nothing is probed again — no paid calls per start.
    calls.clear()
    latest, warnings = models.latest_models(
        cache, NOW + timedelta(minutes=30), flaky, lambda: catalog
    )
    assert calls == []
    assert latest == {"opus": agents.OPUS, "sonnet": agents.SONNET, "codex": codex.CODEX_MODEL}
    assert any("fable" in w for w in warnings)
    # After the back-off only the failed role is retried.
    models.latest_models(cache, NOW + timedelta(hours=2), flaky, lambda: catalog)
    assert calls == ["fable"]


@pytest.mark.parametrize("fetched_at", [None, "not a date", "2026-10-11T11:00:00"])
def test_codex_catalog_without_usable_timestamp_is_unchecked(tmp_path, fetched_at):
    catalog = _catalog("gpt-6-astra", "gpt-7-astra")
    if fetched_at is None:
        del catalog["fetched_at"]
    else:
        catalog["fetched_at"] = fetched_at
    latest, warnings = models.latest_models(
        tmp_path / "check.json", NOW, PINNED_CLAUDE.get, lambda: catalog
    )
    assert "codex" not in latest
    assert any("Zeitstempel" in w for w in warnings)


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
    _seed_cache(cache, NOW.isoformat(), old_pins, pinned=old_pins)
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


# --- Known limitations of 0.29.0 ------------------------------------------------------


def test_naive_cache_timestamp_is_a_cache_miss(tmp_path):
    cache = tmp_path / "check.json"
    _seed_cache(cache, "2026-10-11T11:00:00", dict(PINNED_CLAUDE, opus="claude-opus-6"))
    probed = []

    def probe(alias):
        probed.append(alias)
        return PINNED_CLAUDE[alias]

    latest, _ = models.latest_models(cache, NOW, probe, lambda: None)
    assert probed == list(models.CLAUDE_ALIASES)
    assert latest["opus"] == agents.OPUS


def _probe_forbidden(monkeypatch) -> list[str]:
    """Records probes — a raising fake would be swallowed by the warn-only path."""
    probed: list[str] = []

    def record(alias):
        probed.append(alias)
        return PINNED_CLAUDE[alias]

    monkeypatch.setattr(models, "resolve_claude_alias", record)
    return probed


def test_run_rejects_dirty_checkout_before_probing_models(target_repo, monkeypatch):
    probed = _probe_forbidden(monkeypatch)
    (target_repo / "fremd.py").write_text("dirty\n")
    runner, app = _cli()
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo"])
    assert res.exit_code == 1, res.output
    assert "uncommittete" in res.output
    assert probed == []


def test_resume_rejects_dirty_checkout_before_probing_models(target_repo, monkeypatch):
    from adw.state import RunState

    runner, app = _cli()
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    assert res.exit_code == 2, res.output
    state = RunState.find_latest(target_repo)
    state.dry_run = False
    state.save(target_repo)
    probed = _probe_forbidden(monkeypatch)
    (target_repo / "fremd.py").write_text("dirty\n")
    for command in ("resume", "approve"):
        res = runner.invoke(app, [command, state.run_id, "--repo", str(target_repo)])
        assert res.exit_code == 1, (command, res.output)
        assert "uncommittete" in res.output, command
    assert probed == []


@pytest.mark.parametrize("entry", [
    {"model": None},                                  # failure without error text
    {"model": None, "error": 7},                      # error not a string
    {"model": 5, "error": None},                      # model not a string
])
def test_malformed_cache_entry_is_a_miss_not_a_crash(tmp_path, entry):
    cache = tmp_path / "check.json"
    cache.write_text(json.dumps({
        "pinned": models.pinned_models(),
        "roles": {"opus": dict(entry, checked_at=NOW.isoformat())},
    }))
    probed = []

    def probe(alias):
        probed.append(alias)
        return PINNED_CLAUDE[alias]

    latest, _ = models.latest_models(cache, NOW, probe, lambda: None)
    assert "opus" in probed
    assert latest["opus"] == agents.OPUS



def test_freshest_catalog_wins_and_unreadable_ones_are_skipped(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"models": [], "fetched_at": "2026-10-01T00:00:00Z"}))
    new = tmp_path / "new.json"
    new.write_text(json.dumps({"models": [], "fetched_at": "2026-10-11T00:00:00Z"}))
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    missing = tmp_path / "missing.json"
    chosen = models.freshest_catalog([old, broken, missing, new])
    assert chosen["fetched_at"] == "2026-10-11T00:00:00Z"
    assert models.freshest_catalog([missing]) is None
