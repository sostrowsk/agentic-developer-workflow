"""`adw improve` (W15): weekly improvement report from the ledger, plus at most one
brief draft by the read-only ``improve_agent``. The report is pure code (0 tokens);
report and brief land under ``<ledger>/reports/`` and are committed fail-open."""

import contextlib
import fcntl
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adw import ledger
from adw.agents import REGISTRY, SdkAgentRunner
from adw.git import run_git

LOCK_FILE = ".improve.lock"
MIN_RUNS = 3
TEXT_MAX = 300
MAX_FINDINGS = 20
TOO_FEW = "Zu wenige Läufe für eine Auswertung"
BRIEF_CAUSES = ("orchestrator", "gate", "prompt")
REQUIRED_HEADINGS = ("Ausgangslage", "Aufgabe", "Vorentscheidungen", "Deferred",
                     "Akzeptanzkriterien")
LEVERS = {
    "prompt": "Kandidat für Hill Climbing (W7)",
    "model": "Kandidat für Hill Climbing (W7)",
    "orchestrator": "Brief für einen ADW-Lauf",
    "gate": "Brief für einen ADW-Lauf",
    "context_missing": "Wissen für das Ziel-Repo (Expertise, W9)",
    "target_repo": "Wissen für das Ziel-Repo (Expertise, W9)",
    "issue_quality": "Brief-Vorlage schärfen",
}
# The ADW source tree the package runs from: the agent checks premises there.
ADW_SOURCE = Path(__file__).resolve().parent.parent


class ImproveError(Exception):
    """Missing/unusable ledger or a held lock: the command exits 1."""


@contextlib.contextmanager
def _improve_lock(path: Path):
    """Non-blocking flock on ``<ledger>/.improve.lock`` (pattern of ``_repo_lock``),
    kept out of the ledger's commits via ``.git/info/exclude``."""
    with open(path / LOCK_FILE, "a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImproveError("adw improve läuft bereits für dieses Ledger") from None
        try:
            _exclude_lock_file(path)
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _exclude_lock_file(path: Path) -> None:
    """Only under the held lock: no writes on contention, no racing rewrites."""
    exclude = path / ".git" / "info" / "exclude"
    with contextlib.suppress(OSError):
        lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        if LOCK_FILE not in lines:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("".join(f"{x}\n" for x in [*lines, LOCK_FILE]), encoding="utf-8")


def _cut(value) -> str:
    text = " ".join(str(value).split()) if value is not None else "-"
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1] + "…"


def _num(value) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _cost(record: dict) -> float:
    cost = record.get("cost_usd")
    return float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else 0.0


def _window(records, signals, start, end, repo_slug, *, include_end) -> list[tuple]:
    pairs = []
    for record, signal in zip(records, signals, strict=True):
        # Same eligibility as compute_kpis, so no section sees runs the KPIs skip.
        if not isinstance(record, dict) or type(record.get("schema_version")) is not int \
                or record["schema_version"] != ledger.SCHEMA_VERSION:
            continue
        ts = ledger._parse_ts(record.get("started_at"))
        if ts is None or ts < start or (ts > end if include_end else ts >= end):
            continue
        if repo_slug is not None and record.get("repo_slug") != repo_slug:
            continue
        pairs.append((record, signal))
    return pairs


def _kpis(pairs: list[tuple]) -> dict:
    return ledger.compute_kpis([r for r, _ in pairs], signals=[s for _, s in pairs])


def _kpi_section(current: dict, previous: dict) -> list[str]:
    """The full ``adw kpis`` table (versions + total) and its breakdowns; of the
    previous period only the total row."""
    from adw.cli import _KPI_COLUMNS, _kpi_num, _kpi_pairs, _kpi_pct, _kpi_row

    def table(labelled: list[tuple]) -> list[str]:
        rows = [list(_KPI_COLUMNS)] + [_kpi_row(label, m) for label, m in labelled]
        return ["| " + " | ".join(row) + " |" for row in rows[:1]] + [
            "|" + "---|" * len(_KPI_COLUMNS)
        ] + ["| " + " | ".join(row) + " |" for row in rows[1:]]

    labelled = [(_kpi_num(g["adw_version"]), g) for g in current["groups"]]
    labelled.append(("gesamt", current["total"]))
    total = current["total"]
    lines = ["## Kennzahlen", "", *table(labelled), ""]
    lines.append(
        f"- Retros {total['retro_runs']}: {_kpi_pairs(total['retro_causes'], str)} | "
        f"Bewertungen {_kpi_pairs(total['ratings'], str)} | "
        f"Finding-Feedback {_kpi_pairs(total['finding_feedback'], str)}"
    )
    lines += [
        f"- {label}: Kosten-Anteile {_kpi_pairs(m['agent_cost_shares'], _kpi_pct)}"
        f" | Severity {_kpi_pairs(m['findings_by_severity'], str)}"
        f" | Action {_kpi_pairs(m['findings_by_action'], str)}"
        for label, m in labelled
    ]
    lines += ["", "Vorzeitraum (gleich lang, davor):", "",
              *table([("gesamt", previous["total"])]), ""]
    return lines


def _groups(pairs: list[tuple]) -> list[dict]:
    groups: dict = {}
    for record, signal in pairs:
        retro = signal.get("retro") or {}
        for item in retro.get("items") or []:
            if not isinstance(item, dict) or not isinstance(item.get("cause"), str):
                continue
            group = groups.setdefault(item["cause"], {"cause": item["cause"], "entries": [],
                                                      "runs": {}})
            group["entries"].append((record.get("run_id"), item))
            group["runs"][record.get("run_id")] = _cost(record)
    for group in groups.values():
        group["cost"] = sum(group["runs"].values())
        group["score"] = len(group["runs"]) * group["cost"]
    return sorted(groups.values(), key=lambda g: (-g["score"], g["cause"]))


def _group_lines(group: dict) -> list[str]:
    lines = [
        f"### {group['cause']}",
        "",
        f"Einträge {len(group['entries'])}, Läufe {len(group['runs'])}, "
        f"Kosten {_num(round(group['cost'], 4))} USD",
        "",
    ]
    for run_id, item in group["entries"]:
        evidence = item.get("evidence")
        evidence = evidence if isinstance(evidence, list) else []
        lines += [
            f"- Lauf {_cut(run_id)}",
            f"  - problem: {_cut(item.get('problem'))}",
            f"  - better: {_cut(item.get('better'))}",
            f"  - lever: {_cut(item.get('lever'))}",
            f"  - evidence: {'; '.join(_cut(e) for e in evidence) or '-'}",
        ]
    lines.append("")
    return lines


def _recurring(pairs: list[tuple]) -> list[dict]:
    found: dict = {}
    for record, signal in pairs:
        run_id = record.get("run_id")
        keys = {}
        for f in record.get("findings") or []:
            if isinstance(f, dict) and isinstance(f.get("finding_key"), str):
                keys.setdefault(f["finding_key"], f.get("severity"))
        votes = {}
        for entry in signal.get("feedback", []):
            if entry.get("kind") == "finding" and entry.get("value") in ("valid", "noise"):
                votes.setdefault(entry.get("finding_key"), []).append(entry["value"])
        for key, severity in keys.items():
            data = found.setdefault(key, {"key": key, "runs": set(), "severity": set(),
                                          "valid": 0, "noise": 0})
            data["runs"].add(run_id)
            if isinstance(severity, str):
                data["severity"].add(severity)
            for value in votes.get(key, []):
                data[value] += 1
    hits = [d for d in found.values() if len(d["runs"]) >= 2]
    return sorted(hits, key=lambda d: (-len(d["runs"]), d["key"]))[:MAX_FINDINGS]


def _regressions(pairs: list[tuple]) -> list[tuple]:
    out = []
    for record, signal in pairs:
        reasons = []
        if record.get("phase") == "escalated":
            reasons.append("escalated")
        last = None
        for entry in signal.get("feedback", []):
            if entry.get("kind") == "rate" and entry.get("value") in ("good", "bad"):
                last = entry["value"]
        if last == "bad":
            reasons.append("Bewertung bad")
        if reasons:
            out.append((record, reasons))
    return sorted(out, key=lambda x: str(x[0].get("run_id")))


_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _missing_headings(markdown: str) -> list[str]:
    """Required section names without their own ATX heading outside code fences.
    A heading counts for a name if it starts with it (template suffixes such as
    "Akzeptanzkriterien (messbar)" are fine) — one heading covers one name only."""
    titles, fence = [], None
    for line in markdown.splitlines():
        if (m := _FENCE.match(line)) and (fence is None or m.group(1)[0] == fence):
            fence = None if fence else m.group(1)[0]
            continue
        if fence is None and (m := _HEADING.match(line)):
            titles.append(m.group(1))

    def covers(title: str, name: str) -> bool:
        rest = title[len(name):] if title.startswith(name) else None
        return rest is not None and (not rest or not rest[0].isalnum())

    return [n for n in REQUIRED_HEADINGS if not any(covers(t, n) for t in titles)]


def _brief(group: dict, group_text: str) -> tuple[str | None, str]:
    """(brief markdown or None, note for the report). Never raises."""
    try:
        briefs = ADW_SOURCE / "docs" / "briefs"
        task = (
            "Entwirf einen Brief für die folgende Problemgruppe des ADW-Wochenberichts "
            f"(cause: {group['cause']}).\n\n# Problemgruppe\n\n{group_text}\n\n"
            "# Brief-Vorlage (docs/briefs/README.md)\n\n"
            f"{(briefs / 'README.md').read_text(encoding='utf-8')}\n\n"
            "# Beispiel (docs/briefs/r2-log-integritaet.md)\n\n"
            f"{(briefs / 'r2-log-integritaet.md').read_text(encoding='utf-8')}\n\n"
            'Antworte NUR mit JSON: {"title": str, "brief_markdown": str}.'
        )
        result = SdkAgentRunner().run(REGISTRY["improve_agent"], task, ADW_SOURCE)
        try:
            answer = json.loads(result.text)
        except ValueError:
            return None, "Brief-Entwurf verworfen: Antwort ist kein JSON"
        if not isinstance(answer, dict) or not isinstance(answer.get("title"), str) \
                or not isinstance(answer.get("brief_markdown"), str):
            return None, "Brief-Entwurf verworfen: Felder title/brief_markdown fehlen oder falsch"
        missing = _missing_headings(answer["brief_markdown"])
        if missing:
            return None, f"Brief-Entwurf verworfen: Überschriften fehlen: {', '.join(missing)}"
        title = _cut(answer["title"])
        return answer["brief_markdown"], f"Brief-Entwurf ({group['cause']}): {title}"
    except Exception as exc:  # noqa: BLE001 — the report always comes into being
        return None, f"Brief-Entwurf verworfen: {type(exc).__name__}: {exc}"


def _commit(path: Path, week: str, paths: list[str]) -> None:
    """Commit exactly the report artifacts; whatever else is staged stays staged
    and out of this commit. No change to the artifacts → no commit."""
    def git(*args):
        return run_git(path, *args, timeout=ledger._GIT_TIMEOUT)

    try:
        present = [p for p in paths if (path / p).exists()
                   or git("ls-files", "--error-unmatch", "--", p).returncode == 0]
        if not present:
            return
        status = git("status", "--porcelain", "--", *present)
        if status.returncode != 0:
            raise RuntimeError(status.stderr.strip())
        if not status.stdout.strip():
            return
        for args in (("add", "-A", "--", *present),
                     ("commit", "-m", f"improve {week}", "--only", "--", *present)):
            result = git(*args)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        ledger._warn(f"Ledger-Commit fehlgeschlagen (Bericht bleibt lokal): {exc}")
        return
    ledger._push(path)


def run_improve(since: int, repo_slug: str | None, no_brief: bool) -> list[str]:
    """Writes the weekly report (and brief); returns the stdout lines."""
    try:
        path = ledger._ledger_path()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise ImproveError(f"Ledger nicht lesbar: {exc}") from None
    if path is None:
        raise ImproveError("kein Ledger gefunden (Git-Klon via ADW_LEDGER erwartet)")
    with _improve_lock(path):
        return _improve(path, since, repo_slug, no_brief)


def _improve(path: Path, since: int, repo_slug: str | None, no_brief: bool) -> list[str]:
    now = datetime.now(UTC)
    year, week_no, _ = now.date().isocalendar()
    week = f"{year}-W{week_no:02d}"
    records, signals = ledger._read_ledger_runs(path)
    span = timedelta(days=since)
    current = _window(records, signals, now - span, now, repo_slug, include_end=True)
    previous = _window(records, signals, now - 2 * span, now - span, repo_slug,
                       include_end=False)
    current_kpis = _kpis(current)
    runs = current_kpis["total"]["runs"]
    lines = [f"# ADW-Verbesserungsbericht {week}", "",
             f"Zeitraum: letzte {since} Tage" + (f", Repo {repo_slug}" if repo_slug else ""),
             ""]
    lines += _kpi_section(current_kpis, _kpis(previous))
    out = [f"Läufe im Zeitraum: {runs}"]
    brief = None
    if runs < MIN_RUNS:
        lines += [TOO_FEW, ""]
    else:
        groups = _groups(current)
        lines += ["## Problemgruppen aus Retros", ""]
        texts = {g["cause"]: "\n".join(_group_lines(g)) for g in groups}
        for g in groups:
            lines.append(texts[g["cause"]])
        if not groups:
            lines += ["Keine Retro-Einträge im Zeitraum.", ""]
        recurring = _recurring(current)
        lines += ["## Wiederkehrende Findings", ""]
        lines += [
            f"- `{d['key']}`: Läufe {len(d['runs'])}, Severity "
            f"{', '.join(sorted(d['severity'])) or '-'}, Feedback valid={d['valid']} "
            f"noise={d['noise']}"
            for d in recurring
        ] or ["Keine."]
        regressions = _regressions(current)
        lines += ["", "## Regressionsfall-Kandidaten", ""]
        lines += [
            f"- {_cut(r.get('run_id'))}: {_cut(r.get('issue_title'))} — {', '.join(reasons)}"
            for r, reasons in regressions
        ] or ["Keine."]
        levered = [g for g in groups if g["cause"] in LEVERS]
        lines += ["", "## Hebel je Gruppe", ""]
        lines += [f"- {g['cause']}: {LEVERS[g['cause']]}" for g in levered] or ["Keine."]
        lines.append("")
        eligible = [g for g in groups if g["cause"] in BRIEF_CAUSES]
        if no_brief:
            note = "Kein Brief-Entwurf: --no-brief gesetzt"
        elif not eligible:
            note = "Kein Brief-Entwurf: keine Gruppe mit cause orchestrator, gate oder prompt"
        else:
            brief, note = _brief(eligible[0], texts[eligible[0]["cause"]])
        lines += [note, ""]
        out += [
            f"Problemgruppen: {len(groups)}",
            f"Wiederkehrende Findings: {len(recurring)}",
            f"Regressionsfall-Kandidaten: {len(regressions)}",
            f"Gruppen mit Hebel: {len(levered)}",
        ]
    reports = path / "reports"
    reports.mkdir(exist_ok=True)
    report_path = reports / f"{week}.md"
    brief_path = reports / f"{week}-brief.md"
    report_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    if brief is not None:
        brief_path.write_text(brief.rstrip("\n") + "\n", encoding="utf-8")
    else:
        brief_path.unlink(missing_ok=True)
    _commit(path, week, [f"reports/{week}.md", f"reports/{week}-brief.md"])
    return [str(report_path), *out]
