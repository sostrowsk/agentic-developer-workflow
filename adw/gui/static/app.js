// Vanilla client (GUI-SPEC §7.3, E5): native EventSource + fetch only, no
// framework, no external asset. There is exactly ONE rendering path: the
// server-rendered detail snapshot. The live view never re-implements the tree,
// panes or phase bar in JS (that would drift from the snapshot). Instead the SSE
// stream is a change signal — on each new record the client re-fetches the same
// server-rendered detail page and swaps its live regions in place (no page
// reload). Whatever a freshly opened completed run shows — final answers, gate
// output, review findings, aggregate outcomes, durations, phases, node statuses
// and reader problems — the live view shows identically, because it IS that page
// (AC 14/17/20). Re-rendering the full current snapshot is idempotent, so no
// record is ever lost or shown twice.
(function () {
  "use strict";
  var body = document.body;
  var repo = body.getAttribute("data-repo");
  var runId = body.getAttribute("data-run-id");
  if (!repo || !runId) return;

  // --- client-injected chrome hints (A4): the server delivers them with the page
  // as data attributes on <body>, so what the client writes into the Diff / Tools /
  // Artifacts panels follows the language the server decided on. The English
  // literal stays as the fallback for a page rendered without the attributes.
  // ``el`` scopes the lookup: a hint that belongs to one panel (the diff hints
  // live on the Diff section, which only exists for a bracketed node) is read from
  // the nearest carrier, everything else from <body>.
  function hint(name, fallback, el) {
    var attr = "data-hint-" + name;
    var src = (el && el.closest && el.closest("[" + attr + "]")) || body;
    var value = src.getAttribute(attr);
    return value === null || value === "" ? fallback : value;
  }

  var base = "/api/runs/" + encodeURIComponent(repo) + "/" + encodeURIComponent(runId);
  var detailUrl = "/runs/" + encodeURIComponent(repo) + "/" + encodeURIComponent(runId);
  var streamUrl = base + "/stream";

  // --- Aufgabe C: response-time instrumentation (vanilla performance API only, no
  // dependency, no browser automation). Each instrumented interaction sets a start
  // mark at the triggering input event and an end mark ONLY once the browser has
  // painted the resulting content. Because a requestAnimationFrame callback runs
  // BEFORE the associated paint, the end mark is recorded in a task scheduled from
  // WITHIN a rAF callback (rAF -> setTimeout 0), which runs after that paint.
  // Between the marks a named performance.measure() is created, readable via
  // performance.getEntriesByName(...) and in the browser's Performance panel. The
  // pinned names are documented in docs/gui-response-time.md.
  function perfMark(name) {
    try { performance.mark(name); } catch (e) { /* performance API unavailable */ }
  }
  function perfEndAfterPaint(startMark, endMark, measure, isCurrent) {
    requestAnimationFrame(function () {
      setTimeout(function () {
        // Aufgabe B (P1): re-check currentness INSIDE the post-paint task — a
        // selection that was current when it scheduled this task may have been
        // superseded before the task runs; a superseded interaction records no end
        // mark and no measure.
        if (isCurrent && !isCurrent()) return;
        try {
          performance.mark(endMark);
          performance.measure(measure, startMark, endMark);
        } catch (e) { /* performance API unavailable */ }
      }, 0);
    });
  }
  // Complete the measure only once any asynchronously loaded content is rendered:
  // an interaction that triggers a fetch (node selection -> loadToolBody, tab
  // switch -> loadDiff) passes that load's promise here, and the post-paint end
  // mark is scheduled only after it SETTLES — so a loading placeholder is never
  // mistaken for completion (C1). An interaction with no fetch passes no promise
  // and completes immediately (after the next paint).
  function perfEndAfterContent(startMark, endMark, measure, loadPromise, isCurrent) {
    // Aufgabe B: a superseded interaction (isCurrent() false) records no measure.
    var finish = function () {
      if (isCurrent && !isCurrent()) return;
      perfEndAfterPaint(startMark, endMark, measure, isCurrent);
    };
    if (loadPromise && typeof loadPromise.then === "function") {
      loadPromise.then(finish, finish);  // settle (fulfilled OR rejected), then paint
    } else {
      finish();
    }
  }

  // The live regions replaced on every refresh — the same nodes the server
  // renders for a completed snapshot.
  var REGIONS = ["header.run-header", "main.detail"];

  var pending = null;
  var inFlight = false;
  var repeat = false;

  // --- node-dependent detail pane (AC 14): clicking a tree node shows only that
  // node's pane. The node <li> and its pane share a data-seq, so selection is a
  // pure data-seq match. The choice is preserved across the live region swap.
  // A server ?focus=<seq> navigation (P2) positions the bounded window on a node
  // that was outside it (e.g. a Timeline bar target) and renders both its tree
  // entry and its pane; the client then opens on exactly that node.
  var selectedSeq = body.getAttribute("data-focus") || null;
  // Whether the user has explicitly chosen a node: only then does the context panel
  // follow the selection. Absent an explicit choice the panel shows latest_context
  // (the live / no-selection view, GUI-SPEC §7.2), even though the detail pane
  // auto-opens the first node. A ?focus navigation is itself an explicit choice.
  var contextPinned = !!body.getAttribute("data-focus");

  // Aufgabe B (latest-interaction-wins): a monotonically increasing generation
  // token captured when a selection starts. A selection's asynchronous work — the
  // tool-body fetch write and the post-paint end mark — is applied ONLY while its
  // generation is still the current one, so a superseded selection's late fetch
  // never writes the wrong node into the pane and never produces a measure.
  var selectionGen = 0;

  // --- read-only run-context panel (GUI-SPEC §7.2): the six-field run state at the
  // selected node's seq. The data travels in the render — each node carries its own
  // `data-context`, the no-selection fallback is the body's `data-latest-context` —
  // so this only PROJECTS the chosen node's context onto the fixed field list (no
  // client-side re-derivation). A null field is shown empty, never as 0. Round is a
  // {loop, n, cap} object; every other field is a scalar.
  var CONTEXT_FIELDS = ["phase", "round", "limit_hits", "circuit_breakers", "cost_usd", "followups"];

  function parseContext(raw) {
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (e) { return null; }
  }

  function formatContextValue(field, value) {
    if (value === null || value === undefined) return "";
    if (field === "round") {
      if (typeof value !== "object") return "";
      var loop = (value.loop === null || value.loop === undefined) ? "" : value.loop + " ";
      var n = (value.n === null || value.n === undefined) ? "" : value.n;
      var cap = (value.cap === null || value.cap === undefined) ? "" : value.cap;
      return (n === "" && cap === "") ? loop.trim() : (loop + n + "/" + cap).trim();
    }
    if (field === "cost_usd" && typeof value === "number") {
      return "" + (Math.round(value * 1e6) / 1e6);  // trim float noise, keep the value
    }
    return "" + value;
  }

  // The context to display for the current selection: the selected node's own
  // `data-context`, or `data-latest-context` (live / no selection).
  function contextForSelection() {
    if (contextPinned && selectedSeq) {
      var node = document.querySelector('.node[data-seq="' + selectedSeq + '"][data-context]');
      var ctx = node && parseContext(node.getAttribute("data-context"));
      if (ctx) return ctx;
    }
    return parseContext(body.getAttribute("data-latest-context"));
  }

  function updateContextPanel() {
    var ctx = contextForSelection();
    CONTEXT_FIELDS.forEach(function (field) {
      var slot = document.querySelector('[data-context-field="' + field + '"]');
      if (!slot) return;
      slot.textContent = ctx ? formatContextValue(field, ctx[field]) : "";
    });
  }

  function applySelection() {
    var haveSelected =
      selectedSeq && document.querySelector('.pane[data-seq="' + selectedSeq + '"]');
    if (!haveSelected) {
      var first = document.querySelector(".node[data-seq]");
      selectedSeq = first ? first.getAttribute("data-seq") : null;
    }
    document.querySelectorAll(".panes .pane").forEach(function (pane) {
      pane.classList.toggle("selected", pane.getAttribute("data-seq") === selectedSeq);
    });
    document.querySelectorAll(".node[data-seq]").forEach(function (node) {
      node.classList.toggle("selected", node.getAttribute("data-seq") === selectedSeq);
    });
    // A tool-node's own pane holds a standalone (not inside <details>) load anchor;
    // the toggle-based lazy load never fires for it, so load it on selection —
    // otherwise selecting a tool node directly would show a permanently empty box.
    // Return the load promise so a caller (the adw:select measure) can complete
    // only once the fetched pane content is inserted, not when the box is empty.
    if (selectedSeq) {
      var selectedPane = document.querySelector('.pane[data-seq="' + selectedSeq + '"]');
      var pre = selectedPane && selectedPane.querySelector(".tool-detail pre[data-load-seq]");
      if (pre) return loadToolBody(pre, true);  // selection-triggered -> guarded
    }
    return Promise.resolve();
  }

  // Delegated on document so it keeps working after main.detail is swapped. A
  // timeline bar (Aufgabe A) carries its target node's data-seq: clicking it
  // navigates to that node in the Trace tab (switch to Trace, then select it),
  // reusing the same data-seq selection path (A5). Node selection is instrumented
  // with the adw:select measure (Aufgabe C).
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    // A5: the phase fold caret toggles collapse only — it must not select the node
    // it sits on (original-node clicks stay unchanged).
    if (target.closest("[data-fold-toggle]")) return;
    var bar = target.closest(".tl-bar[data-seq]");
    var node = target.closest(".node[data-seq]");
    if (!bar && !node) return;
    // P2: a Timeline bar may target a node outside the current bounded window, so it
    // has no pane here. Selecting it in place would silently fall back to the first
    // visible node (the WRONG node). Instead navigate the server window to it via
    // ?focus, which materialises both its tree entry and its pane on load.
    if (bar) {
      var barSeq = bar.getAttribute("data-seq");
      if (!document.querySelector('.pane[data-seq="' + barSeq + '"]')) {
        window.location.assign(detailUrl + "?focus=" + encodeURIComponent(barSeq));
        return;
      }
    }
    // Latest-interaction-wins (Aufgabe B): capture this selection's generation; its
    // async work applies only while it stays current.
    var gen = ++selectionGen;
    var isCurrent = function () { return gen === selectionGen; };
    perfMark("adw:select:start");
    if (bar) {
      selectedSeq = bar.getAttribute("data-seq");
      activateRunTab("trace");  // reveal the node in the Trace tab
    } else {
      selectedSeq = node.getAttribute("data-seq");
    }
    // The chosen node now drives the read-only context panel (time travel).
    contextPinned = true;
    updateContextPanel();
    // Complete the measure only after the (possibly fetched) detail pane content is
    // rendered — applySelection returns the tool-body load promise when the
    // selected node lazy-loads its payload (C1) — and only if this selection is
    // still the current one (B2).
    perfEndAfterContent("adw:select:start", "adw:select:end", "adw:select", applySelection(), isCurrent);
  });

  // --- switchable tabs (Aufgabe D + the run-level Raw / node-level Diff tabs):
  // exactly one panel active at a time. Delegated so it survives the live region
  // swap; toggles classes only (panels are server-rendered). Tab groups nest (the
  // agent.run tabs live inside the run-level tabs), so only members whose nearest
  // [data-tabs] is THIS group are toggled — a nested group is left untouched.
  function activateTab(tabs, name) {
    tabs.querySelectorAll(".tab-btn").forEach(function (b) {
      if (b.closest("[data-tabs]") === tabs)
        b.classList.toggle("active", b.getAttribute("data-tab") === name);
    });
    var active = null;
    tabs.querySelectorAll("[data-tab-panel]").forEach(function (panel) {
      if (panel.closest("[data-tabs]") !== tabs) return;
      var on = panel.getAttribute("data-tab-panel") === name;
      panel.classList.toggle("active", on);
      if (on) active = panel;
    });
    // The Diff patch is fetched on demand from the read-only diff endpoint so a
    // large patch never inlines into the initial page (Aufgabe B7). Return the
    // load's promise so the adw:tab measure completes only after the patch renders;
    // a tab with no fetch returns an already-resolved promise (immediate).
    if (active && name === "diff") return loadDiff(active);
    return Promise.resolve();
  }

  // Switch the run-level tab group (used by timeline bar-click navigation).
  function activateRunTab(name) {
    var group = document.querySelector(".run-tabs[data-tabs]");
    if (group) activateTab(group, name);
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest(".tab-btn") : null;
    if (!btn) return;
    var tabs = btn.closest("[data-tabs]");
    if (!tabs) return;
    perfMark("adw:tab:start");
    // Complete the measure only after the target tab's content is rendered —
    // activateTab returns the diff load promise for the Diff tab, else resolves
    // immediately (C1).
    perfEndAfterContent("adw:tab:start", "adw:tab:end", "adw:tab", activateTab(tabs, btn.getAttribute("data-tab")));
  });

  // --- A5: default-fold of phases (pure client state, no persistence). The tree
  // opens with phases collapsed; only the server-named default phase (the one with
  // the tree-order-first error, else the last-started) — and the phase containing a
  // ?focus target — starts open. Group/repeat wrappers collapse natively via
  // <details>. A phase's subtree is the run of DIRECT trace-list rows after it whose
  // depth is greater than the phase's, up to the next same-or-shallower row; those
  // are shown/hidden together. Phases whose subtree begins before the loaded page
  // simply have no rows to fold and stay visible (E3).
  function parentOf(n) {
    return n.parentElement || n.parentNode || n.parent || null;
  }
  function directRows(list) {
    var out = [];
    for (var i = 0; i < list.children.length; i++) {
      if (list.children[i].tagName === "LI") out.push(list.children[i]);
    }
    return out;
  }
  function rowDepth(li) {
    // Read --depth from the inline style STRING (works in a real browser and in the
    // test harness, neither of which is assumed to expose a CSSOM style object here).
    var m = (li.getAttribute("style") || "").match(/--depth:\s*(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
  }
  function phaseEnd(rows, idx, depth) {
    var k = idx + 1;
    while (k < rows.length && rowDepth(rows[k]) > depth) k++;
    return k;  // exclusive
  }
  function setPhaseOpen(rows, idx, open) {
    var li = rows[idx];
    var depth = rowDepth(li);
    var end = phaseEnd(rows, idx, depth);
    li.classList.toggle("phase-open", open);
    var caret = li.querySelector("[data-fold-toggle]");
    if (caret) caret.setAttribute("aria-expanded", open ? "true" : "false");
    for (var k = idx + 1; k < end; k++) rows[k].classList.toggle("fold-hidden", !open);
  }
  // A5: make a ?focus target visible by opening EVERY fold ancestor on the loaded
  // page — its enclosing group and/or repetition <details> (found recursively, not
  // only among the phase's direct rows) and the phase whose range contains it — so a
  // node nested inside a collapsed phase, group and repetition is never left hidden.
  function openFocusPath(list, rows) {
    var focusSeq = body.getAttribute("data-focus");
    if (!focusSeq) return;
    var elem = document.querySelector('.trace-list [data-seq="' + focusSeq + '"]');
    if (!elem) return;
    // Open each collapsible <details> ancestor (group / repetition).
    var n = elem;
    while (n && n !== list) {
      if (n.tagName === "DETAILS" && n.classList.contains("trace-collapse")) n.open = true;
      n = parentOf(n);
    }
    // The direct trace-list row on the target's ancestor chain, and the phase whose
    // range contains it — open that phase (its rows may start before this page, in
    // which case there is simply no governing phase row to open).
    var row = elem;
    while (row && parentOf(row) !== list) row = parentOf(row);
    var ri = row ? rows.indexOf(row) : -1;
    if (ri === -1) return;
    for (var p = 0; p <= ri; p++) {
      if (rows[p].getAttribute("data-node-type") !== "phase") continue;
      if (ri < phaseEnd(rows, p, rowDepth(rows[p]))) { setPhaseOpen(rows, p, true); break; }
    }
  }
  function initTreeFold() {
    var list = document.querySelector(".trace-list");
    if (!list) return;
    var openSeq = list.getAttribute("data-default-phase");
    var rows = directRows(list);
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute("data-node-type") !== "phase") continue;
      setPhaseOpen(rows, i, rows[i].getAttribute("data-seq") === openSeq);
    }
    openFocusPath(list, rows);  // reveal a ?focus target through every fold ancestor
  }
  document.addEventListener("click", function (event) {
    var caret = event.target.closest ? event.target.closest("[data-fold-toggle]") : null;
    if (!caret) return;
    var li = caret.closest(".node[data-node-type='phase']");
    var list = li && li.closest(".trace-list");
    if (!li || !list) return;
    var rows = directRows(list);
    var idx = rows.indexOf(li);
    if (idx !== -1) setPhaseOpen(rows, idx, !li.classList.contains("phase-open"));
  });

  // The recovery card's escalation-report link opens the Artifacts tab and reveals
  // the escalation.md entry — read-only navigation through the EXISTING tab/artifact
  // machinery (no navigation, no fetch of its own): opening the <details> fires the
  // native toggle the artifact loader already listens for. If the artifact is
  // missing there is no such entry and the tab's existing "missing" state stands.
  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("[data-recovery-artifact]") : null;
    if (!link) return;
    event.preventDefault();
    activateRunTab("artifacts");
    // Match the entry by attribute VALUE (not by embedding the artifact name — which
    // contains a `.` — into a selector) so a filename with dots resolves reliably.
    var name = link.getAttribute("data-recovery-artifact");
    var summary = null;
    document.querySelectorAll("summary[data-artifact]").forEach(function (s) {
      if (!summary && s.getAttribute("data-artifact") === name) summary = s;
    });
    if (summary) {
      var details = summary.closest("details");
      if (details) details.open = true;  // opening fires toggle -> loadArtifact
    }
  });

  // --- node-level Diff tab (Aufgabe B): request exactly this node's derived
  // from/to snapshot pair and render the changed-file list plus the patch. Own
  // means only — no third-party highlighter (E5).
  function loadDiff(panel) {
    if (panel.getAttribute("data-loaded")) return Promise.resolve();
    var frm = panel.getAttribute("data-diff-from");
    var to = panel.getAttribute("data-diff-to");
    var body = panel.querySelector(".diff-body");
    if (!frm || !to || !body) return Promise.resolve();
    panel.setAttribute("data-loaded", "1");
    body.textContent = hint("loading", "Loading…");
    // The returned promise resolves once the patch is rendered, so a caller (the
    // adw:tab measure) completes only after the diff content is in the DOM (C1).
    return fetch(base + "/diff?from=" + encodeURIComponent(frm) + "&to=" + encodeURIComponent(to))
      .then(function (response) {
        if (!response.ok) throw new Error("diff " + response.status);
        return response.json();
      })
      .then(function (data) { renderDiff(body, data); })
      .catch(function () {
        panel.removeAttribute("data-loaded");
        body.textContent = hint("diff-failed", "(failed to load the diff — open the Diff tab again to retry)", body);
      });
  }

  function renderDiff(body, data) {
    var files = (data && data.files) || [];
    var patch = (data && data.patch) || "";
    if (files.length === 0 && !patch) {
      body.textContent = hint("no-changes", "No changes in this step.", body);
      return;
    }
    // Rendered as text into the existing <pre> (no divergent DOM construction):
    // one file-summary line per changed file, then the unified patch. A binary
    // file shows "bin" instead of inventing numeric counts (AC-B1/E5).
    var header = files.map(function (f) {
      var add = (f.additions === null || f.additions === undefined) ? "bin" : "+" + f.additions;
      var del = (f.deletions === null || f.deletions === undefined) ? "" : " -" + f.deletions;
      return f.path + "  " + add + del;
    });
    body.textContent = header.join("\n") + "\n\n" + patch;
  }

  // --- lazy tool payloads (Aufgabe B): full tool inputs/results are NOT inlined
  // in the initial page (that caused the ~35 s freeze). Each collapsed entry
  // carries only its data-load-seq; the full payload is fetched from the read-only
  // events route on first expand, so selecting an agent.run node never blocks on
  // rendering megabytes at once, yet every full payload stays reachable.
  // ``guarded`` marks a SELECTION-triggered load (its payload is written only while
  // its node is still the selected one); an expand-triggered load (the <details>
  // toggle) is unguarded and always renders its own entry.
  function loadToolBody(pre, guarded) {
    var seq = pre.getAttribute("data-load-seq");
    if (!seq) return Promise.resolve();
    // A fetch for this pre is already in flight: reuse its promise so a caller (the
    // adw:select measure) completes only when the content actually RENDERS — never
    // on the "Loading…" placeholder, even when the same node is re-selected while
    // its first fetch is still pending (double-click / A->B->A) (P2, C1).
    if (pre._loadPromise) return pre._loadPromise;
    if (pre.getAttribute("data-loaded")) return Promise.resolve();  // already rendered
    pre.setAttribute("data-loaded", "1");
    pre.textContent = hint("loading", "Loading…");
    // Fetch ONLY this record (from_seq == to_seq), not the whole tail from seq to
    // the log end — expanding an early entry must not transfer the entire log.
    var promise = fetch(base + "/events?from_seq=" + encodeURIComponent(seq) + "&to_seq=" + encodeURIComponent(seq))
      .then(function (response) { return response.json(); })
      .then(function (records) {
        pre._loadPromise = null;
        // Aufgabe B: a GUARDED load renders only if ITS node is STILL the selected
        // one — so a superseded selection (a NEWER node chosen) writes nothing and
        // restores the unloaded state, yet a re-selection of the SAME node still
        // renders and never clears the last-chosen node's pane (P2). Keyed on node
        // identity, not generation, so re-selecting the same node is not "stale".
        if (guarded && String(seq) !== String(selectedSeq)) {
          pre.removeAttribute("data-loaded");
          pre.textContent = "";
          return;
        }
        var found = null;
        for (var i = 0; i < records.length; i++) {
          if (String(records[i].seq) === String(seq)) { found = records[i]; break; }
        }
        pre.textContent = found
          ? JSON.stringify(found.payload, null, 2)
          : hint("payload-missing", "(payload not found)");
      })
      .catch(function () {
        pre._loadPromise = null;
        pre.removeAttribute("data-loaded"); // allow a retry on the next expand
        if (guarded && String(seq) !== String(selectedSeq)) return;
        pre.textContent = hint("load-failed-expand", "(failed to load — expand again to retry)");
      });
    pre._loadPromise = promise;
    return promise;
  }

  // --- Aufgabe B: the Artifacts tab loads a whitelisted artifact's content on
  // demand from the read-only artifacts route. The full content is NOT inlined in
  // the initial page (bounded initial render, E8): only a bounded portion is
  // inserted, the rest reachable through an explicit "Show more" — no content is
  // dropped. Rendered as faithful monospace text via textContent (E10, escaped).
  var ARTIFACT_CHUNK = 20000;

  // Show the first `shown` characters in the <pre> and reveal the server-rendered
  // "Show more" anchor while content remains. No DOM is constructed in JS (the one
  // shared server-snapshot rendering path stays authoritative, GUI-SPEC §7.3): the
  // full text is held in a JS property and sliced into the existing <pre>.
  function showArtifactSlice(pre, more, shown) {
    var text = pre._fullText || "";
    pre.textContent = text.slice(0, shown);
    if (more) {
      if (text.length > shown) {
        more._nextShown = shown + ARTIFACT_CHUNK;
        more.hidden = false;
      } else {
        more.hidden = true;
      }
    }
  }

  function loadArtifact(summary) {
    if (summary.getAttribute("data-loaded")) return Promise.resolve();
    var name = summary.getAttribute("data-artifact");
    var wrap = summary.closest(".artifact-wrap");
    var pre = wrap ? wrap.querySelector("[data-artifact-body]") : null;
    var more = wrap ? wrap.querySelector("[data-artifact-more]") : null;
    if (!name || !pre) return Promise.resolve();
    summary.setAttribute("data-loaded", "1");
    pre.textContent = hint("loading", "Loading…");
    // The returned promise resolves once the bounded initial slice is inserted, so
    // the adw:artifact measure (Aufgabe C) completes only after the fetched content
    // is rendered — a loading indicator is not completion (C1).
    return fetch(base + "/artifacts/" + encodeURIComponent(name))
      .then(function (response) {
        if (!response.ok) throw new Error("artifact " + response.status);
        return response.text();
      })
      .then(function (text) {
        pre._fullText = text;
        showArtifactSlice(pre, more, ARTIFACT_CHUNK);
      })
      .catch(function () {
        summary.removeAttribute("data-loaded");  // allow a retry on re-open
        pre.textContent = hint("load-failed-open", "(failed to load — open again to retry)");
      });
  }

  // "Show more" reveals the next bounded chunk of an already-fetched artifact.
  document.addEventListener("click", function (event) {
    var more = event.target.closest ? event.target.closest("[data-artifact-more]") : null;
    if (!more) return;
    event.preventDefault();
    var wrap = more.closest(".artifact-wrap");
    var pre = wrap ? wrap.querySelector("[data-artifact-body]") : null;
    if (pre) showArtifactSlice(pre, more, more._nextShown || ARTIFACT_CHUNK);
  });

  // The native <details> toggle event does not bubble, so listen in the capture
  // phase; load the body only when a details element is being opened.
  document.addEventListener("toggle", function (event) {
    var details = event.target;
    if (!details || details.tagName !== "DETAILS" || !details.open) return;
    var pre = details.querySelector ? details.querySelector("pre[data-load-seq]") : null;
    if (pre) loadToolBody(pre);
    var summary = details.querySelector ? details.querySelector("summary[data-artifact]") : null;
    // Aufgabe C: opening an artifact is the third instrumented interaction. Same
    // construction as adw:select / adw:tab — start mark at the opening event, end
    // mark + measure only after the fetched content is painted (rAF -> task after
    // paint), the async load promise gating completion (C1).
    if (summary) {
      perfMark("adw:artifact:start");
      perfEndAfterContent("adw:artifact:start", "adw:artifact:end", "adw:artifact", loadArtifact(summary));
    }
  }, true);

  // --- Raw tab (Aufgabe C): filter the server-rendered rows by type and by free
  // text over the payload. Toggling `hidden` only — no DOM construction, so the
  // single server-rendered snapshot path stays authoritative (GUI-SPEC §7.3). The
  // rest of a large log is reached via the server-rendered "Load more" links.
  function applyRawFilter() {
    var typeSel = document.querySelector(".raw-type-filter");
    var search = document.querySelector(".raw-search");
    var rows = document.querySelectorAll(".raw-list .raw-row");
    if (!rows.length) return;
    var type = typeSel ? typeSel.value : "";
    var q = search ? search.value.toLowerCase() : "";
    var any = false;
    rows.forEach(function (row) {
      var okType = !type || row.getAttribute("data-type") === type;
      var okText = !q || row.textContent.toLowerCase().indexOf(q) !== -1;
      var show = okType && okText;
      row.hidden = !show;
      if (show) any = true;
    });
    var empty = document.querySelector(".raw-empty");
    if (empty) empty.hidden = any;
  }

  // Delegated so the filters keep working after the live region swap. Typing
  // narrows the loaded rows instantly (over the previews); the `type` select and
  // the "Search all" submit run the exhaustive server-side filter over the FULL
  // payloads (a match beyond the preview is found there).
  document.addEventListener("input", function (event) {
    if (event.target.closest && event.target.closest(".raw-controls")) applyRawFilter();
  });
  document.addEventListener("change", function (event) {
    var sel = event.target.closest ? event.target.closest(".raw-type-filter") : null;
    if (sel && sel.form) sel.form.submit();  // exhaustive server-side type filter
  });

  // Record the open/closed state of the expandable <details> in the swapped region
  // — the Tools entries, the Raw rows and the artifact wraps (the flat, windowed
  // trace tree itself is not collapsible) — so the wholesale live region swap does
  // not reset the user's expand choices (GUI-SPEC §7.3). Each is keyed by a stable
  // identifier of its own content (its lazy-load seq or its artifact name), so an
  // entry unseen before keeps the server default. No DOM is built here: state only.
  function detailsKey(details) {
    var art = details.querySelector("summary[data-artifact]");
    if (art) return "art:" + art.getAttribute("data-artifact");
    var pre = details.querySelector("pre[data-load-seq]");
    if (pre) {
      var kind = details.classList && details.classList.contains("raw-full-wrap") ? "raw" : "tool";
      return kind + ":" + pre.getAttribute("data-load-seq");
    }
    return null;
  }

  function captureOpenState() {
    var state = {};
    document.querySelectorAll("details").forEach(function (details) {
      var key = detailsKey(details);
      if (key) state[key] = details.open;
    });
    return state;
  }

  function reapplyOpenState(doc, state) {
    doc.querySelectorAll("details").forEach(function (details) {
      var key = detailsKey(details);
      if (key && Object.prototype.hasOwnProperty.call(state, key)) {
        details.open = state[key];
      }
    });
  }

  function swapRegions(html) {
    var openState = captureOpenState();
    var doc = new DOMParser().parseFromString(html, "text/html");
    reapplyOpenState(doc, openState); // preserve collapse choices before swapping
    // The context panel's no-selection fallback (`data-latest-context`) lives on
    // <body>, which is NOT one of the swapped regions — so refresh it from the
    // fetched document, otherwise an unselected live panel would freeze at the
    // value the page was first opened with.
    var freshBody = doc.querySelector && doc.querySelector("body");
    if (freshBody && freshBody.getAttribute) {
      var latest = freshBody.getAttribute("data-latest-context");
      if (latest !== null) body.setAttribute("data-latest-context", latest);
    }
    REGIONS.forEach(function (selector) {
      var next = doc.querySelector(selector);
      var current = document.querySelector(selector);
      if (next && current) current.replaceWith(next);
    });
    // Bump the generation so any pending selection's measure is superseded by the
    // refresh, then re-apply the current selection to the fresh markup. Obsolete
    // tool-body writes are prevented by the node-identity guard in loadToolBody
    // (Aufgabe B / P1): a fetch whose node is no longer selected writes nothing.
    ++selectionGen;
    applySelection();
    updateContextPanel();  // re-project the context onto the swapped-in panel
    initTreeFold();        // re-apply the default fold to the swapped-in tree (A5)
  }

  ++selectionGen;
  applySelection(); // initial: select the root node's pane
  updateContextPanel(); // initial: latest_context (no explicit selection yet)
  initTreeFold(); // initial: phases collapsed except the default-open / focused one

  function refresh() {
    if (inFlight) { repeat = true; return; }
    inFlight = true;
    // Re-fetch the window the user is ACTUALLY looking at: the paged position
    // lives in the query string (`offset`, `tools_offset`, `focus`). Dropping it
    // would make the server render its default first window, and the wholesale
    // region swap would discard the user's position — the moving window is what
    // makes the bounded entry-node budget reachable. Read per refresh, since the
    // window links are ordinary navigations that re-enter this script.
    fetch(detailUrl + (window.location.search || ""), {
      headers: { "X-Requested-With": "fetch" },
    })
      .then(function (response) { return response.text(); })
      .then(swapRegions)
      .catch(function () { /* transient read error: keep the last good view */ })
      .then(function () {
        inFlight = false;
        if (repeat) { repeat = false; scheduleRefresh(); }
      });
  }

  // Coalesce bursts of records into a single re-render (the snapshot is whole).
  function scheduleRefresh() {
    if (pending) return;
    pending = setTimeout(function () { pending = null; refresh(); }, 200);
  }

  var source = new EventSource(streamUrl);

  source.onmessage = function (event) {
    scheduleRefresh();
    var record;
    try {
      record = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    if (record.type === "run" && record.kind === "end") {
      // Render the final completed snapshot, then stop tailing.
      setTimeout(refresh, 250);
      source.close();
    }
  };

  // Reader problems (broken lines, seq gaps) surface in the same snapshot, so a
  // live problem becomes visible on the next refresh — no separate render path.
  source.addEventListener("problem", function () {
    scheduleRefresh();
  });
})();
