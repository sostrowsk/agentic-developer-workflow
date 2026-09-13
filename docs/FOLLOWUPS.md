# Follow-up register

Consolidated on 2026-09-13. Every `[P*]` finding that ADW runs left behind in
`.adw/runs/*/followups.md`, verified against the code as it stands and given a
disposition. Run artifacts are historical records and were **not** edited — this
file is the single place that says what is still open.

Runs whose `followups.md` was pruned away are gone with it; only the eight runs
still on disk are listed.

**Why a register:** eight files nobody reads accumulate findings that are long
fixed, long obsolete, or quietly important. Two of the eleven below had in fact
already been fixed, four were obsolete, and one — the 5xx class — turned out to
be far larger than its finding described.

## Disposition

| # | Run | Sev | Finding | Disposition |
|---|-----|-----|---------|-------------|
| 1 | `16f39431` | P2 | Missing test: `?focus` on a node whose phase starts before the loaded page | **Obsolete** |
| 2 | `72a042ad` | P2 | Self-heal restores from the index, not HEAD | **Fixed — 0.21.4** |
| 3 | `7fe9d702` | P2 | A4 wall-clock evidence uses a fixture, not a real run | **Deferred** |
| 4 | `81795e53` | P2 | Non-mapping payload 5xx's the run detail | **Fixed — 0.21.2 / 0.21.3** |
| 5 | `9c452d1d` | P3 | Plan added i18n work beyond the contract | **Obsolete** |
| 6 | `b6739174` | P2 | Live refresh discards tab/expansion/pending payload | **Partly obsolete, remainder open** |
| 7 | `b6739174` | P3 | `has_trace` derived from `bool(events)` | **Fixed — 0.21.4** |
| 8 | `b6739174` | P2 ×2 | Manual 2-second browser check not documented in the report | **Obsolete** |
| 9 | `d1c9de00` | P2 | Retention dirty-check not atomic across a run's worktrees | **Open, unreachable here** |
| 10 | `d1c9de00` | P3 | Hard-coded English strings in `app.js` | **Fixed** (earlier, undated) |
| 11 | `d1c9de00` | P3 ×2 | Missing DoD tests: auto-prune fail-open, exact `--older-than` boundary | **Fixed — 0.21.4** |

## The reasoning

**1 — Obsolete.** The finding asks for a test of a `?focus`/`?offset` window whose
governing phase row lies outside the loaded page. Since 0.18.0 the trace tree is
not paged at all: `?offset` is accepted and ignored, and the whole tree renders.
The premise no longer exists, so neither does the test.

**2 — Fixed in 0.21.4.** `git checkout -- <path>` restores from the **index**. A
*staged* modification, deletion or rename of an ADW artifact therefore survived
the heal, the following status check still saw a dirty tree, and the command
refused instead of healing and proceeding. Now `git checkout HEAD -- <path>`
(index *and* worktree, path-scoped); a path absent from HEAD has its index entry
dropped with `git rm --cached --ignore-unmatch` before the file is removed. Two
regression tests: staged modification and staged deletion.

**3 — Deferred, and it cannot be closed here.** A4 asks for a measurement on a real
run with ≥ 2000 tool nodes. No such run exists — the largest on record has 786.
Beyond that, the `performance` measures are not observable through browser
automation because `requestAnimationFrame` does not fire in a hidden tab. This
stays open until a genuinely large run happens; manufacturing one would produce
exactly the fixture evidence the finding rejects.

**4 — Fixed, and it was the tip of an iceberg.** The finding named two helpers.
0.21.2 fixed those; a sweep of the whole read surface then found ten more
unguarded payload accesses plus two `node.end_payload` consumers. Measured against
a run whose every event carries a non-mapping payload, three of the four read
endpoints returned 500 — the run list worst of all, where one corrupt run took
every healthy run off the home page. Closed by 0.21.3.

**5, 8 — Obsolete.** Both concern the `.adw/plan.md` and `.adw/report.md` of runs
long finished. Those artifacts are historical records of what those runs did;
editing them now would falsify the record and change nothing about the code.

**6 — Partly obsolete, remainder needs its own investigation.** The finding
predates the trace rewrite of 0.17.0–0.21.0. The part about the active tab and
expanded entries is addressed: `reapplyOpenState()` restores collapse choices
across the live region swap, and the node selection survives it by data-seq match.
What is *not* demonstrably handled is the narrow race the finding also names — a
lazy payload request in flight while the region is replaced, whose response then
lands in a detached `<pre>`. That deserves a reproduction before a fix, not a
blind patch, and it is not worth a run of its own until someone sees it happen.

**9 — Open, but unreachable in this repo.** The claim is architecturally true: with
multiple worktrees, retention can remove an early clean one and then meet a later
one that turned dirty, reporting the run `skipped` though part of it is gone.
Reaching it requires a run with more than one worktree, i.e. `--parallel` — which
this repo's config cannot do (single `backend` lane by design, so that GUI
templates and CSS are not split across worktrees). A real fix means one mutation
critical section across all of a run's worktrees, or honest partial-failure
reporting with exit code 1. Worth doing if ADW is ever run parallel against a
target repo; not worth it for this one.

**10 — Fixed earlier.** `app.js` now reads its chrome strings through `hint(name,
fallback, el)`, which looks up `data-hint-*` attributes rendered server-side from
the i18n catalogue, with the English literal as fallback. Six such attributes are
wired in `run_detail.html`.

**11 — Fixed in 0.21.4.** Both were coverage gaps against existing, correct code,
so both tests were green on first run — which is the right outcome for a missing
*proof* rather than a missing fix. Each was mutation-checked to confirm it bites:
flipping `rec.date <= threshold` to `<` turns the boundary test red.

## Still open after this pass

- **#3** — A4 evidence, waiting on a genuinely large run.
- **#6** — the lazy-payload-during-refresh race, needs a reproduction first.
- **#9** — retention atomicity across multiple worktrees, needs `--parallel` to matter.
