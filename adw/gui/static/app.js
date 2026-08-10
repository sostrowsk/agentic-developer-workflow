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

  // The live regions replaced on every refresh — the same nodes the server
  // renders for a completed snapshot.
  var REGIONS = ["header.run-header", "main.detail"];

  var pending = null;
  var inFlight = false;
  var repeat = false;

  // --- node-dependent detail pane (AC 14): clicking a tree node shows only that
  // node's pane. The node <li> and its pane share a data-seq, so selection is a
  // pure data-seq match. The choice is preserved across the live region swap.
  var selectedSeq = null;

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
    if (selectedSeq) {
      var selectedPane = document.querySelector('.pane[data-seq="' + selectedSeq + '"]');
      var pre = selectedPane && selectedPane.querySelector(".tool-detail pre[data-load-seq]");
      if (pre) loadToolBody(pre);
    }
  }

  // Delegated on document so it keeps working after main.detail is swapped.
  document.addEventListener("click", function (event) {
    var node = event.target.closest ? event.target.closest(".node[data-seq]") : null;
    if (!node) return;
    selectedSeq = node.getAttribute("data-seq");
    applySelection();
  });

  // --- switchable tabs (Aufgabe D + the run-level Raw / node-level Diff tabs):
  // exactly one panel active at a time. Delegated so it survives the live region
  // swap; toggles classes only (panels are server-rendered). Tab groups nest (the
  // agent.run tabs live inside the run-level tabs), so only members whose nearest
  // [data-tabs] is THIS group are toggled — a nested group is left untouched.
  document.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest(".tab-btn") : null;
    if (!btn) return;
    var tabs = btn.closest("[data-tabs]");
    if (!tabs) return;
    var name = btn.getAttribute("data-tab");
    tabs.querySelectorAll(".tab-btn").forEach(function (b) {
      if (b.closest("[data-tabs]") === tabs) b.classList.toggle("active", b === btn);
    });
    var active = null;
    tabs.querySelectorAll("[data-tab-panel]").forEach(function (panel) {
      if (panel.closest("[data-tabs]") !== tabs) return;
      var on = panel.getAttribute("data-tab-panel") === name;
      panel.classList.toggle("active", on);
      if (on) active = panel;
    });
    // The Diff patch is fetched on demand from the read-only diff endpoint so a
    // large patch never inlines into the initial page (Aufgabe B7).
    if (active && name === "diff") loadDiff(active);
  });

  // --- node-level Diff tab (Aufgabe B): request exactly this node's derived
  // from/to snapshot pair and render the changed-file list plus the patch. Own
  // means only — no third-party highlighter (E5).
  function loadDiff(panel) {
    if (panel.getAttribute("data-loaded")) return;
    var frm = panel.getAttribute("data-diff-from");
    var to = panel.getAttribute("data-diff-to");
    var body = panel.querySelector(".diff-body");
    if (!frm || !to || !body) return;
    panel.setAttribute("data-loaded", "1");
    body.textContent = "Loading…";
    fetch(base + "/diff?from=" + encodeURIComponent(frm) + "&to=" + encodeURIComponent(to))
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
  function loadToolBody(pre) {
    if (pre.getAttribute("data-loaded")) return;
    var seq = pre.getAttribute("data-load-seq");
    if (!seq) return;
    pre.setAttribute("data-loaded", "1");
    pre.textContent = "Loading…";
    // Fetch ONLY this record (from_seq == to_seq), not the whole tail from seq to
    // the log end — expanding an early entry must not transfer the entire log.
    fetch(base + "/events?from_seq=" + encodeURIComponent(seq) + "&to_seq=" + encodeURIComponent(seq))
      .then(function (response) { return response.json(); })
      .then(function (records) {
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
        pre.textContent = "(failed to load — expand again to retry)";
      });
  }

  // The native <details> toggle event does not bubble, so listen in the capture
  // phase; load the body only when a details element is being opened.
  document.addEventListener("toggle", function (event) {
    var details = event.target;
    if (!details || details.tagName !== "DETAILS" || !details.open) return;
    var pre = details.querySelector ? details.querySelector("pre[data-load-seq]") : null;
    if (pre) loadToolBody(pre);
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
    applySelection(); // re-apply the current selection to the fresh markup
  }

  applySelection(); // initial: select the root node's pane

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
