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

  // Aufgabe B (latest-interaction-wins): a monotonically increasing generation
  // token captured when a selection starts. A selection's asynchronous work — the
  // tool-body fetch write and the post-paint end mark — is applied ONLY while its
  // generation is still the current one, so a superseded selection's late fetch
  // never writes the wrong node into the pane and never produces a measure.
  var selectionGen = 0;

  function applySelection(gen) {
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
      if (pre) return loadToolBody(pre, gen);
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
    // Complete the measure only after the (possibly fetched) detail pane content is
    // rendered — applySelection returns the tool-body load promise when the
    // selected node lazy-loads its payload (C1) — and only if this selection is
    // still the current one (B2).
    perfEndAfterContent("adw:select:start", "adw:select:end", "adw:select", applySelection(gen), isCurrent);
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
    body.textContent = "Loading…";
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
        body.textContent = "(failed to load the diff — open the Diff tab again to retry)";
      });
  }

  function renderDiff(body, data) {
    var files = (data && data.files) || [];
    var patch = (data && data.patch) || "";
    if (files.length === 0 && !patch) {
      body.textContent = "No changes in this step.";
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
  function loadToolBody(pre, gen) {
    if (pre.getAttribute("data-loaded")) return Promise.resolve();
    var seq = pre.getAttribute("data-load-seq");
    if (!seq) return Promise.resolve();
    pre.setAttribute("data-loaded", "1");
    pre.textContent = "Loading…";
    // Fetch ONLY this record (from_seq == to_seq), not the whole tail from seq to
    // the log end — expanding an early entry must not transfer the entire log. The
    // returned promise resolves once the payload has been inserted into the pane,
    // so a caller can time the interaction to that render (C1).
    return fetch(base + "/events?from_seq=" + encodeURIComponent(seq) + "&to_seq=" + encodeURIComponent(seq))
      .then(function (response) { return response.json(); })
      .then(function (records) {
        // Aufgabe B: a superseded selection's late fetch must NOT write into the
        // DOM while a newer node is selected — a debug tool must never show the
        // data of the wrong node. Discard the (successful) stale response but
        // RESTORE the unloaded state so re-selecting this node re-fetches instead
        // of staying stuck at "Loading…" forever (P2).
        if (gen !== undefined && gen !== selectionGen) {
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
          : "(payload not found)";
      })
      .catch(function () {
        pre.removeAttribute("data-loaded"); // allow a retry on the next expand
        if (gen !== undefined && gen !== selectionGen) return;
        pre.textContent = "(failed to load — expand again to retry)";
      });
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
    pre.textContent = "Loading…";
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
        pre.textContent = "(failed to load — open again to retry)";
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

  // Record every node's <details> open/closed state, keyed by data-seq, so the
  // wholesale region swap does not reset the user's expand/collapse choices
  // (the server always renders <details open>). Nodes unseen before keep the
  // server default (GUI-SPEC §7.3 / AC 13: the tree stays collapsible while live).
  function captureOpenState() {
    var state = {};
    document.querySelectorAll(".node[data-seq]").forEach(function (node) {
      var details = node.querySelector(":scope > details");
      if (details) state[node.getAttribute("data-seq")] = details.open;
    });
    return state;
  }

  function reapplyOpenState(doc, state) {
    doc.querySelectorAll(".node[data-seq]").forEach(function (node) {
      var seq = node.getAttribute("data-seq");
      var details = node.querySelector(":scope > details");
      if (details && Object.prototype.hasOwnProperty.call(state, seq)) {
        details.open = state[seq];
      }
    });
  }

  function swapRegions(html) {
    var openState = captureOpenState();
    var doc = new DOMParser().parseFromString(html, "text/html");
    reapplyOpenState(doc, openState); // preserve collapse choices before swapping
    REGIONS.forEach(function (selector) {
      var next = doc.querySelector(selector);
      var current = document.querySelector(selector);
      if (next && current) current.replaceWith(next);
    });
    // Re-apply the current selection to the fresh markup under a FRESH generation
    // (Aufgabe B / P1): the swap can start a tool-body fetch, and tying it to a
    // generation lets a newer selection supersede it — otherwise its obsolete
    // payload would be written into the refreshed DOM after the newer selection.
    applySelection(++selectionGen);
  }

  applySelection(++selectionGen); // initial: select the root node's pane (generation-tied)

  function refresh() {
    if (inFlight) { repeat = true; return; }
    inFlight = true;
    fetch(detailUrl, { headers: { "X-Requested-With": "fetch" } })
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
