# CLAUDE.md — ADW-Orchestrator

## Versionierung & Changelog (Release-Prozess, verbindlich)

Jeder Push nach `main` ist ein Release mit eigener Revisionsnummer (SemVer
0.x): **Feature-Push → Minor-Bump, reiner Doku-/Fix-Push → Patch-Bump.**

Vor jedem Push:

1. **Version bumpen** — NUR in `pyproject.toml` (`project.version`).
   `adw/__init__.py` liest die Version aus den Package-Metadaten
   (`importlib.metadata`) — dort nie ein Literal pflegen.
   Regressionstest: `tests/test_smoke.py::test_version_matches_pyproject`.
2. **Changelog nachziehen** — `CHANGELOG.md` (EN) **und** `CHANGELOG.de.md`
   (DE) synchron halten (Keep-a-Changelog-Format): die `Unreleased`-Sektion
   der neuen Version bekommt Versionsnummer + Datum, Compare-Link unten
   ergänzen. Neue Arbeit nach dem Release sammelt sich wieder in einer
   `[X.Y.Z] — Unreleased`-Sektion.
3. **Tag setzen** — `git tag vX.Y.Z` auf dem Release-Commit.
4. **Push** — `git push && git push --tags`.

Historie: Die Versionen bis 0.2.1 wurden rückwirkend aus der Push-Historie
vergeben (`git reflog show origin/main`); ihre Tags zeigen auf die damals
gepushten Stände.
