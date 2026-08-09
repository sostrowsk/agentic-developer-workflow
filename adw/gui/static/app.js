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
  }

  // Delegated on document so it keeps working after main.detail is swapped.
  document.addEventListener("click", function (event) {
    var node = event.target.closest ? event.target.closest(".node[data-seq]") : null;
    if (!node) return;
    selectedSeq = node.getAttribute("data-seq");
    applySelection();
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
