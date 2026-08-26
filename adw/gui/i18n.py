"""GUI internationalisation (de/en) for the Run Inspector chrome (§7.5).

A plain dictionary module — no i18n library, no gettext, no .po files, no runtime
dependency (E1). Only UI CHROME is translated (labels, tab names, column headers,
navigation and hint texts); CONTENT (prompts, agent output, findings, artifact
bodies, gate output, raw payloads) is never touched (E2).

Language is chosen per request in exactly this order (A2):
``?lang=de|en`` → language cookie → first supported ``Accept-Language`` → ``en``.
"""

SUPPORTED = ("en", "de")
DEFAULT = "en"
# The cookie name is an internal detail (not pinned by the contract).
LANG_COOKIE = "adw_lang"


# The chrome catalog: identical key sets for both languages, every value a
# non-empty string, no German value a mere copy of the English text (A1/A6).
# Language-neutral loanwords / technical identifiers (Prompt, Tools, Trace,
# Timeline, Diff, Details, Repo, Issue, Phase, Status, Start, exit_code:) stay
# identical — they are single tokens, never multi-word phrases.
_EN: dict[str, str] = {
    "runs_title": "ADW Runs",
    "col_run": "Run",
    "col_repo": "Repo",
    "col_issue": "Issue",
    "col_phase": "Phase",
    "col_status": "Status",
    "col_start": "Start",
    "col_duration": "Duration",
    "col_cost": "Cost",
    "col_events": "Events",
    "repo_unavailable": "Repository unavailable:",
    "no_trace": "no trace",
    "no_trace_title": "no event log for this run",
    "no_trace_line": "no trace — no event log for this run",
    # Labels for the newly visible states (§7.2): a tree node that is technically
    # WAITING, a run/phase pausing for a human APPROVAL. `awaiting_approval` is the
    # only state that needs a person to act and is emphasised the strongest.
    "status_waiting": "waiting",
    "status_awaiting": "awaiting",
    "status_awaiting_approval": "awaiting approval",
    # A short label (not translated prose) marking a simulated (dry) run on the run
    # list row and in the run-detail header banner. A conventional single-token
    # loanword that reads identically in both languages.
    "dry_run": "Dry-Run",
    "lang_switch": "Deutsch",
    "tab_trace": "Trace",
    "tab_timeline": "Timeline",
    "tab_artifacts": "Artifacts",
    "tab_raw": "Raw",
    "tab_prompt": "Prompt",
    "tab_answer": "Answer",
    "tab_tools": "Tools",
    "tab_diff": "Diff",
    "tab_details": "Details",
    "nav_previous": "previous",
    "nav_more": "more",
    "nav_of": "of",
    "reader_problems": "Reader problems",
    "no_problems": "No problems reported.",
    "raw_all_types": "(all types)",
    "raw_type": "type",
    "raw_filter_placeholder": "filter payload text…",
    "raw_search_all": "Search all",
    "raw_no_matches": "No matching events.",
    "raw_load_more": "Load more",
    "raw_hidden": "hidden",
    "raw_jump": "raw log",
    "raw_range": "seq range",
    "raw_range_clear": "clear range",
    "prompt_diff_none": "No previous run of this agent in this lane — nothing to diff.",
    "prompt_diff_identical": "Prompt identical to the previous run.",
    "prompt_diff_heading": "Diff against the previous run:",
    "diff_hint": "Open the Diff tab to load the changes for this step.",
    "gate_command": "command:",
    "gate_exit": "exit_code:",
    "agg_duration": "duration:",
    "agg_cost": "cost:",
    "agg_round": "round:",
    "agg_outcome": "outcome:",
    "artifact_open": "open",
    "artifact_open_draft": "open draft",
    "artifact_missing": "missing",
    "artifact_show_more": "Show more",
    "find_severity": "severity",
    "find_key": "key",
    "find_file": "file",
    "find_message": "message",
    "tl_duration": "duration",
    "tl_cost": "cost",
    "tl_tokens": "tokens",
    # Labels for the read-only run-context panel (§7.2): the run state at the seq of
    # the selected node (or the latest event when nothing is selected).
    "ctx_title": "Run context",
    "ctx_phase": "Phase",
    "ctx_round": "Round",
    "ctx_limit_hits": "Limit hits",
    "ctx_circuit_breakers": "Circuit breakers",
    "ctx_cost": "Cost",
    "ctx_followups": "Follow-ups",
    # Hints the CLIENT injects into the Diff / Tools / Artifacts panels. They are
    # chrome, not content — "hint_no_changes" in particular is the PERMANENT result
    # of an empty diff, not a transient state. Delivered with the page as data
    # attributes so app.js renders them in the language the server decided on.
    "hint_loading": "Loading…",
    "hint_no_changes": "No changes in this step.",
    "hint_diff_failed": "(failed to load the diff — open the Diff tab again to retry)",
    "hint_payload_missing": "(payload not found)",
    "hint_load_failed_expand": "(failed to load — expand again to retry)",
    "hint_load_failed_open": "(failed to load — open again to retry)",
    # Labels for the recovery card (§7.2): the next step a run that needs human
    # intervention points to. The command line itself, event values, run_id and repo
    # path are CONTENT and stay untranslated — only these chrome labels switch.
    "recovery_title": "Recovery",
    "recovery_reason": "Reason",
    "recovery_phase": "Affected phase",
    "recovery_aborts": "Abort events",
    "recovery_command": "Next command",
    "recovery_new_run": "This run has escalated — a new run is required.",
    "recovery_report": "Escalation report",
    # Plan-skeleton chrome (§7.2): the heading of the per-lane planned-task list and
    # the coarse lane-level status markers. The task texts themselves are CONTENT and
    # stay untranslated — only these chrome labels switch.
    "plan_skeleton_title": "Planned tasks",
    "plan_skeleton_pending": "pending",
    "plan_skeleton_done": "done",
    # Change-scope chrome (§7.2): the heading of the change-scope block, the +/-
    # column headers and the fallback texts. Only these labels switch language — the
    # file paths and the declared contract scope are CONTENT and stay untranslated.
    "change_scope_title": "Change scope",
    "change_scope_col_add": "+",
    "change_scope_col_del": "−",
    "change_scope_binary": "not numerically available",
    "change_scope_no_diff": "no diff available",
    "change_scope_no_files": "no changed files found",
    "change_scope_no_run_diff": "no run diff available",
    "change_scope_declared": "Declared scope",
    "change_scope_no_declared": "no declared scope",
}

_DE: dict[str, str] = {
    "runs_title": "ADW-Läufe",
    "col_run": "Lauf",
    "col_repo": "Repo",
    "col_issue": "Issue",
    "col_phase": "Phase",
    "col_status": "Status",
    "col_start": "Start",
    "col_duration": "Dauer",
    "col_cost": "Kosten",
    "col_events": "Ereignisse",
    "repo_unavailable": "Repository nicht verfügbar:",
    "no_trace": "kein Trace",
    "no_trace_title": "kein Ereignis-Log für diesen Lauf",
    "no_trace_line": "kein Trace — kein Ereignis-Log für diesen Lauf",
    "status_waiting": "wartet",
    "status_awaiting": "wartet",
    "status_awaiting_approval": "wartet auf Freigabe",
    # Single-token loanword — identical in both languages (see the i18n parity test).
    "dry_run": "Dry-Run",
    "lang_switch": "English",
    "tab_trace": "Trace",
    "tab_timeline": "Timeline",
    "tab_artifacts": "Artefakte",
    "tab_raw": "Roh",
    "tab_prompt": "Prompt",
    "tab_answer": "Antwort",
    "tab_tools": "Tools",
    "tab_diff": "Diff",
    "tab_details": "Details",
    "nav_previous": "zurück",
    "nav_more": "mehr",
    "nav_of": "von",
    "reader_problems": "Reader-Probleme",
    "no_problems": "Keine Probleme gemeldet.",
    "raw_all_types": "(alle Typen)",
    "raw_type": "Typ",
    "raw_filter_placeholder": "Payload-Text filtern…",
    "raw_search_all": "Alle durchsuchen",
    "raw_no_matches": "Keine passenden Ereignisse.",
    "raw_load_more": "Mehr laden",
    "raw_hidden": "verborgen",
    "raw_jump": "Roh-Log",
    "raw_range": "Seq-Bereich",
    "raw_range_clear": "Bereich aufheben",
    "prompt_diff_none": "Kein vorheriger Lauf dieses Agenten in dieser Lane — kein Diff.",
    "prompt_diff_identical": "Prompt identisch zum vorherigen Lauf.",
    "prompt_diff_heading": "Diff gegen den vorherigen Lauf:",
    "diff_hint": "Diff-Reiter öffnen, um die Änderungen dieses Schritts zu laden.",
    "gate_command": "Befehl:",
    "gate_exit": "exit_code:",
    "agg_duration": "Dauer:",
    "agg_cost": "Kosten:",
    "agg_round": "Runde:",
    "agg_outcome": "Ergebnis:",
    "artifact_open": "öffnen",
    "artifact_open_draft": "Entwurf öffnen",
    "artifact_missing": "fehlt",
    "artifact_show_more": "Mehr anzeigen",
    "find_severity": "Schweregrad",
    "find_key": "Schlüssel",
    "find_file": "Datei",
    "find_message": "Meldung",
    "tl_duration": "Dauer",
    "tl_cost": "Kosten",
    "tl_tokens": "Tokens",
    "ctx_title": "Lauf-Zustand",
    "ctx_phase": "Phase",
    "ctx_round": "Runde",
    "ctx_limit_hits": "Limit-Treffer",
    "ctx_circuit_breakers": "Circuit-Breaker",
    "ctx_cost": "Kosten",
    "ctx_followups": "Follow-ups",
    "hint_loading": "Wird geladen…",
    "hint_no_changes": "Keine Änderungen in diesem Schritt.",
    "hint_diff_failed": "(Diff konnte nicht geladen werden — Diff-Reiter erneut öffnen)",
    "hint_payload_missing": "(kein Inhalt gefunden)",
    "hint_load_failed_expand": "(Laden fehlgeschlagen — erneut aufklappen)",
    "hint_load_failed_open": "(Laden fehlgeschlagen — erneut öffnen)",
    "recovery_title": "Recovery",
    "recovery_reason": "Grund",
    "recovery_phase": "Betroffene Phase",
    "recovery_aborts": "Abbruch-Ereignisse",
    "recovery_command": "Nächstes Kommando",
    "recovery_new_run": "Dieser Lauf ist eskaliert — ein neuer Lauf ist nötig.",
    "recovery_report": "Eskalationsbericht",
    "plan_skeleton_title": "Geplante Aufgaben",
    "plan_skeleton_pending": "ausstehend",
    "plan_skeleton_done": "erledigt",
    "change_scope_title": "Änderungsumfang",
    "change_scope_col_add": "+",
    "change_scope_col_del": "−",
    "change_scope_binary": "nicht numerisch verfügbar",
    "change_scope_no_diff": "kein Diff verfügbar",
    "change_scope_no_files": "keine geänderten Dateien gefunden",
    "change_scope_no_run_diff": "kein Lauf-Diff verfügbar",
    "change_scope_declared": "Deklarierter Scope",
    "change_scope_no_declared": "kein deklarierter Scope",
}

CATALOG: dict[str, dict[str, str]] = {"en": _EN, "de": _DE}


def _normalise(value) -> str | None:
    """A supported language code from a raw value, or None."""
    if isinstance(value, str) and value.strip().lower() in SUPPORTED:
        return value.strip().lower()
    return None


def _from_accept_language(header) -> str | None:
    """The first SUPPORTED language named in an ``Accept-Language`` header, in the
    header's own order (a leading unsupported entry is skipped)."""
    if not isinstance(header, str) or not header:
        return None
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()
        if not code:
            continue
        primary = code.split("-")[0]
        if primary in SUPPORTED:
            return primary
    return None


def query_language(request) -> str | None:
    """The explicitly and VALIDLY selected ``?lang=`` language, or None. Only such a
    selection sets the cookie (A3); an invalid value sets nothing."""
    return _normalise(request.query_params.get("lang"))


def select_language(request) -> str:
    """The request's language by the pinned order: ?lang → cookie → Accept-Language
    → en (A2). Unsupported/absent values at one stage fall through to the next."""
    explicit = query_language(request)
    if explicit is not None:
        return explicit
    cookie = _normalise(request.cookies.get(LANG_COOKIE))
    if cookie is not None:
        return cookie
    header = _from_accept_language(request.headers.get("accept-language"))
    if header is not None:
        return header
    return DEFAULT


def other_language(lang: str) -> str:
    """The language the header switch link toggles to."""
    return "en" if lang == "de" else "de"
