# ADW Control Flow — The Seven Phases Explained Simply

> Audience: beginners without programming knowledge.
> Source: `docs/SPEC.md` (as of 2026-07-14).

## What is this all about?

The **Agentic Developer Workflow (ADW)** is a program that turns a task
(an "issue", e.g. *"Add a search feature to the app"*) **fully automatically**
into finished, reviewed code — from the first description all the way to
delivery on a test server.

You can think of it like a **construction site**:

| Role on the construction site | Role in the ADW | Who does it? |
|---|---|---|
| Site manager (organizes everything, makes no technical decisions) | The **Orchestrator** — a fixed program | Deterministic code |
| Two architects who draw the blueprint independently | Spec/Plan agent **and** Codex as a second author | AI (Opus 4.8 / Codex) |
| Lead architect who makes one blueprint out of the two drafts | Spec/Plan synthesis | AI (Fable 5) |
| Construction workers (actually build) | Build agents | AI (Opus 4.8) |
| Building inspectors / TÜV (inspect, but never build themselves) | Codex reviewer & final reviewer | AI (Codex / Fable 5) |
| Checklists & measuring devices (always the same checks) | **Gates** (automated tests) | Deterministic code |

**The most important principle:** *Control flow is code, not prompt.*
That means: **who acts when is decided by a fixed program** — not the AI.
The AI is used only where judgment is needed (writing, building,
evaluating). Everything repeatable (running tests, passing results along, counting
loops) runs as ordinary program code: free, reliable, always the same.

Two more iron rules:

1. **Reviewers never repair.** Whoever finds a defect only reports it. Repairs
   are always done by the build agents — and every repair must afterwards **go through
   all checks again**. No shortcuts, not even for "small things".
2. **Everything has a limit.** Every loop may only repeat a limited number of times.
   If the limit is exhausted — or a repair round achieves *nothing at all* anymore
   ("circuit breaker", like a fuse in the fuse box) — the run aborts
   in a controlled way and writes an **escalation report**: what was accomplished,
   what is open, what went wrong. A human then takes over.

---

## The path through the seven phases

```
Issue ──▶ 1 Spec ──▶ 2 Plan+Contract ──▶ [STOP: human approves] ──▶ 3 Build
                                                                        │
              7 Push+CI ◀── 6 Final Review ◀── 5 Code Review ◀── 4 Integration+E2E
                  │
                  ▼
          Green pipeline + staging = done ✔
```

Almost every phase has small **feedback loops** (defect found →
back to whoever caused it → rework → check again). The details now follow
phase by phase.

---

### Phase 1 — Spec: "What should be built in the first place?"

**Input:** the issue — a text, either typed in directly or fetched from GitLab/GitHub.

1. **Two drafts at the same time:** the **Spec agent** (AI) reads the issue and the
   project and writes a **draft specification**: the goal, what is in scope, what is
   explicitly *not* in scope, and how you can tell it is done ("acceptance criteria").
   **In parallel, Codex writes its own draft** of the same specification, from the same
   issue but without seeing the other draft. Both drafts are stored side by side in the
   run folder (`.adw/runs/<run_id>/drafts/`). Neither author is allowed to build anything.
2. **The synthesis** (AI) reads the issue and *both* drafts and makes **one**
   specification out of them (`.adw/spec.md`): per section the better formulation wins,
   and a point that only one draft saw is kept if it holds up against the issue. It is
   deliberately **not** a merge of everything — everything carried over has to earn its
   place. In addition, the synthesis writes a short **summary**
   (`.adw/spec-summary.md`): what and why, key decisions, what was deliberately left out,
   which draft contributed what, and what is still open. That summary is what you read
   later at the approval stop.
3. The **Codex reviewer** (now in its reviewer role) checks the finished specification.
4. If it finds defects, they go **back to the same synthesis agent** — which keeps
   its "memory" from the first round (session resume) and reworks.
5. This repeats until the reviewer says **"ok"** — at most **5 rounds**, with
   the bar descending per round (details in phase 5).

> Like two editors each writing their own version of a text, an editor-in-chief making the
> best version out of both — and a proofreader who keeps handing it back until it is
> clean, but never writes on it himself.

> **If Codex fails as an author** (e.g. the tool is unavailable), nothing breaks: a note
> is recorded and the synthesis works from the Claude draft alone — and says so in the
> summary. Only the *missing Claude draft* aborts the run: without any draft there is
> nothing to synthesize.

### Phase 2 — Plan + Contract: "How will it be built?"

1. Exactly the same two-step as in phase 1: the **Plan agent** (AI) and **Codex** each
   turn the specification into their own draft of a **step-by-step blueprint**
   (`.adw/plan.md`) and a **contract** (`.adw/contract.yaml`) — in parallel and
   independently. The contract is like a binding power-socket standard: it defines
   exactly how the parts (e.g. the interface and the server logic) must fit together
   later — so that two teams working separately don't deliver incompatible parts in the end.
2. The **plan synthesis** merges both drafts into the final plan and contract and writes
   the summary `.adw/plan-summary.md`.
3. The Codex reviewer checks plan and contract **together**, again in the
   loop until "ok" (same 5-round rule).
4. **Plan approval gate — the built-in STOP:** The workflow **halts**,
   saves its complete state and exits. Now a
   **human** reads the summary and the plan and decides. Only the command `adw approve`
   (or `adw resume`) lets the run continue at exactly this point.
   (Anyone who fully trusts the automation can disable the stop with
   `--no-approval`.)

> This is the only planned point where the human is asked *in the middle of* the
> process — before the expensive build phase begins.

### Phase 3 — Build: "Now it gets built."

1. The **Dispatch** (fixed program, no AI) breaks the plan down into
   **work packages** — e.g. "frontend" (the visible part) and "backend" (the
   logic behind it). Each package gets its own **Lane**:
   its own working folder (Git worktree), its own AI session and
   its own network ports. This way the lanes **cannot get in each
   other's way**. (Without `--parallel` there is simply just one lane —
   same process, one track.)
2. In each lane, the **lane loop** runs:
   - The **Build agent** (AI) writes code — strictly following plan and contract.
   - Then the **Gates** run: automated checks (formatting,
     code style, tests), configured firmly in the project. Like inspection stations
     on an assembly line.
   - **Red?** The error messages go back **to the same
     AI session** as a new task — it still knows its own code and reworks.
   - This repeats, **at most 10 times**. After that: escalation report,
     abort, human takes over.

### Phase 4 — Integration + E2E: "Do the parts fit together?" (only with `--parallel`)

1. A fixed program **merges** the results of all lanes onto a
   shared integration branch.
2. Then an **end-to-end test** (Playwright) runs: a robot clicks its way through
   the finished application like a real user.
3. **If something fails**, the **E2E triage agent** (AI, read-only)
   assigns each failure to the **responsible lane** — like a doctor who only
   diagnoses but does not operate.
4. The affected lane repairs (again via its normal lane loop from
   Phase 3, including all gates), then everything is **re-integrated and re-
   tested**. At most **10 rounds**.

### Phase 5 — Codex Code Review: "Independent quality control."

1. The **Codex reviewer** reads all the new code (read-only!) and
   delivers a structured defect list: what is the problem, how severe is
   it (P1 = critical … P3 = minor), which lane is responsible, and a
   **repair suggestion** (`remediation_plan`).
2. A fixed program **distributes** the defects to the
   right build agents based on the lane field.
3. Important: the repair suggestion is a **recommendation, not an order**. The
   build agent checks it against the specification and project rules and may
   repair **differently, with justification** — after all, the reviewer only sees the code from the outside.
4. After each repair: all gates, then another review — **until "ok"**, at most
   **5 rounds**. The bar descends per round: round 1 fixes all defects, round 2
   only critical and medium ones (P1+P2), from round 3 only critical ones (P1)
   — smaller points are recorded as known limitations instead of being polished
   forever.
5. So the reviewer does not keep flagging the same things, from round 2 on it
   receives the **defect list of the previous rounds including the reasoning**
   ("fixed" or "deliberately not adopted, because …").

### Phase 6 — Final Review + Triage: "Was the right thing actually built?"

Phase 5 asked: *Is the code well made?* Phase 6 asks: *Does it do what the
specification says?*

1. The **final reviewer** (AI, strictly read-only) compares the finished
   implementation with the specification from Phase 1 and reports deviations —
   each with a **category**.
2. The **Triage** (fixed program) sorts by category:
   - **`scope_gap`** — "Something is missing that was never in the plan" →
     is noted as a **follow-up issue** (a report for later). It does **not**
     restart automatically, otherwise the run would grow endlessly.
   - **`implementation` / `trivial`** — "Built, but wrong/sloppy" →
     back to the responsible lane, normal repair cycle with all gates.
3. At most **3 repair cycles**, then escalation.

### Phase 7 — Push + CI: "Deliver and observe."

1. The finished branch is **pushed** (uploaded) to the central repository.
2. There, the project's **CI pipeline** (GitLab or
   GitHub) starts automatically: it builds everything again neutrally, tests, and deploys the
   result to the **staging server** (the test environment).
3. The ADW **polls the status every 60 seconds** — until everything is green,
   but at most for **45 minutes**.
4. **If the pipeline turns red**, the **log analyst** (AI, read-only) reads the
   error logs, turns them into structured findings with lane assignment —
   and the process jumps **back to Phase 3/4**: repair, check, push
   again.
5. **Pipeline green + staging deploy green → the run has finished successfully.**

---

## The safety nets (apply everywhere)

| Mechanism | What it means |
|---|---|
| **Limits** | Phase 3: max. 10 fix iterations · Phase 4: max. 10 rounds · Phase 5: max. 5 review rounds · Phase 6: max. 3 cycles · Phase 7: max. 45 min waiting |
| **Circuit breaker** | If a repair round resolves *nothing* → immediate abort instead of pointless spinning |
| **Escalation report** | Every abort produces `escalation.md`: what was achieved, what is open, why — the handover to the human |
| **Checkpoints** | After **every phase transition** the complete state is saved. `adw resume` continues **exactly there** after a crash, a pause or an exhausted AI quota — like a save point in a video game |
| **Structured handovers** | All reviewers report findings in a fixed data format (JSON). If a response cannot be parsed cleanly, it counts as an **error** — better to ask once too often than to wave through a false "ok" |
| **Permissions** | Every AI gets only the tools its role needs: reviewers may only read, build agents may only write in their own folder |

## The whole thing in one sentence

> A fixed program takes a task through seven stations — describing and
> planning (each twice independently, merged into a best-of version, with human
> approval), building, integrating, two independent
> reviews, delivering — and lets AI work only where judgment
> is needed, while every loop has a limit, every finding has a fixed
> way back, and every abort has a report.
