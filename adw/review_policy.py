"""Review-loop policy and findings bookkeeping: severity floor per round, findings
memory with dispositions, circuit-breaker keys, follow-up report."""



from adw.context import (
    RunContext,
)
from adw.findings import (
    Finding,
    ReviewResult,
)


def _finding_key(item: Finding) -> str:
    return f"{item.file}|{item.issue}"


def _finding_keys(review: ReviewResult) -> list[str]:
    return sorted(_finding_key(f) for f in review.findings)


def _circuit_breaker_keys(ctx: RunContext, findings: list[Finding]) -> list[str]:
    """Baseline for the Circuit-Breaker AFTER a dispatch: deferred Findings
    triggered no fix, so their unchanged reappearance is not a standstill."""
    return sorted(_finding_key(f) for f in _without_deferred(ctx, findings))


def _without_deferred(ctx: RunContext, findings: list[Finding]) -> list[Finding]:
    """Drop Findings that a fix run has already proven unactionable.

    They are in the follow-up report; dispatching them again would burn a fix
    cycle without effect and trip the Circuit-Breaker on the next round.
    Deferred is the P3 ASSESSMENT, not the location: if a later review rates
    the same problem P1/P2, it goes through the fix cycle after all."""
    deferred = set(ctx.state.deferred_findings)
    return [
        item for item in findings if item.severity != "P3" or _finding_key(item) not in deferred
    ]


def _actionable_severities(round_no: int) -> set[str]:
    """Descending severity floor per review round (Review-Policy v2).

    Round 1 takes everything, from round 2 P3 drops out, from round 3 P2 as
    well. Without this floor the loop never converges: a deterministic
    reviewer keeps finding new edge cases at the lowest severity."""
    if round_no <= 1:
        return {"P1", "P2", "P3"}
    if round_no == 2:
        return {"P1", "P2"}
    return {"P1"}


def _split_by_severity(
    findings: list[Finding], round_no: int
) -> tuple[list[Finding], list[Finding]]:
    """(actionable, dropped) for this round's severity floor."""
    allowed = _actionable_severities(round_no)
    actionable = [item for item in findings if item.severity in allowed]
    dropped = [item for item in findings if item.severity not in allowed]
    return actionable, dropped


def _disposition_line(item: Finding, disposition: str) -> str:
    return f"- [{item.severity}] {item.file}: {item.issue} — disposition: {disposition}"


def _dispatched_disposition(round_no: int) -> str:
    return f"fix dispatched (round {round_no})"


def _below_threshold_disposition(round_no: int) -> str:
    return f"not adopted, below severity threshold (round {round_no}); recorded as follow-up"


_DEFERRED_DISPOSITION = "rejected by a fix run as unactionable; recorded as follow-up"


def _severity_context(round_no: int, cap: int, prior: list[str]) -> str | None:
    """Review context from round 2 on: severity floor of THIS round plus the
    findings of previous rounds with their dispositions.

    None without prior findings — round 1 acts on everything, so the prompt
    stays exactly as it was before the policy."""
    if not prior:
        return None
    severities = "/".join(sorted(_actionable_severities(round_no)))
    lines = "\n".join(prior)
    return (
        f"This is review round {round_no} of max {cap}. Only findings of severity "
        f"{severities} will be acted on in this round; anything below is recorded "
        f"as a follow-up, not fixed.\n"
        f"Findings from previous rounds and their dispositions:\n{lines}\n"
        f"Do NOT report these findings again unless the problem demonstrably "
        f"persists in the current version."
    )


def _extend_context(prior: list[str], lines: list[str]) -> list[str]:
    """Append new disposition lines without duplicates — a deterministic
    reviewer reports the same finding again in every round."""
    merged = list(prior)
    merged.extend(line for line in lines if line not in merged)
    return merged


def _findings_text(review: ReviewResult) -> str:
    lines = []
    for item in review.findings:
        plan = "; ".join(item.remediation_plan)
        lines.append(f"- [{item.severity}] {item.file}: {item.issue} (Recommendation: {plan})")
    return "\n".join(lines)


def _write_followups(ctx: RunContext, followups: list[Finding]) -> None:
    """Collect deferred Findings as a follow-up report (no auto-restart):
    scope gaps and P3s that a fix run could not act on.

    Dedupe via the exact file+issue (`_finding_key`): a deliberately unfixed
    scope_gap shows up again in every further review round and must not flood
    the report — but an issue that merely prefixes a recorded one is new."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.run_dir / "followups.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries = []
    written: list[Finding] = []
    seen: set[str] = set()
    for item in followups:
        # Each entry is "- [sev] file: issue" directly followed by its plan line —
        # matching up to that line break makes the comparison exact.
        recorded = f"] {item.file}: {item.issue}\n  - Plan:" in existing
        if recorded or _finding_key(item) in seen:
            continue
        seen.add(_finding_key(item))
        plan = "; ".join(item.remediation_plan)
        entries.append(f"- [{item.severity}] {item.file}: {item.issue}\n  - Plan: {plan}")
        written.append(item)
    if not entries:
        return
    header = "" if existing else f"# Follow-ups — Run {ctx.state.run_id}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(header + "\n".join(entries) + "\n")
    # AFTER the follow-up was actually recorded (AC 9), one point per new entry.
    span_id = ctx.emitter.current_span_id()
    for item in written:
        ctx.emitter.emit(
            "followup",
            {"finding_key": _finding_key(item), "text": item.issue},
            span=span_id,
        )
