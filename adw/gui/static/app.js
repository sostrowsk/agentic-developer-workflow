// Vanilla client (GUI-SPEC §7.3, E5): fetch is unused here — the snapshot is
// server-rendered — and the live tail uses the native EventSource only. New
// records are merged by integer seq so an event already covered by the initial
// snapshot (or delivered twice) is never rendered twice; problem messages are
// appended to the visible problem list without reloading the page.
(function () {
  "use strict";
  var body = document.body;
  var repo = body.getAttribute("data-repo");
  var runId = body.getAttribute("data-run-id");
  if (!repo || !runId) return;

  var seen = new Set();
  var dataEl = document.getElementById("run-data");
  if (dataEl) {
    try {
      var snapshot = JSON.parse(dataEl.textContent);
      (function walk(nodes) {
        (nodes || []).forEach(function (n) {
          if (typeof n.seq === "number") seen.add(n.seq);
          walk(n.children);
        });
      })(snapshot.tree);
    } catch (err) {
      /* a malformed snapshot must not break the live stream */
    }
  }

  var url = "/api/runs/" + encodeURIComponent(repo) + "/" + encodeURIComponent(runId) + "/stream";
  var source = new EventSource(url);

  source.onmessage = function (event) {
    var record;
    try {
      record = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    if (typeof record.seq === "number") {
      if (seen.has(record.seq)) return; // no duplication, no gap
      seen.add(record.seq);
    }
    // Hand the new record to whatever incremental view is listening.
    document.dispatchEvent(new CustomEvent("adw:event", { detail: record }));
    if (record.type === "run" && record.kind === "end") {
      source.close();
    }
  };

  source.addEventListener("problem", function (event) {
    var problem;
    try {
      problem = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    var section = document.querySelector(".problems");
    if (!section) return;
    var list = section.querySelector(".problem-list");
    if (!list) {
      list = document.createElement("ul");
      list.className = "problem-list";
      section.appendChild(list);
    }
    var item = document.createElement("li");
    item.className = "problem";
    item.textContent = problem.kind + (problem.line_no != null ? " · line " + problem.line_no : "");
    list.appendChild(item);
  });
})();
