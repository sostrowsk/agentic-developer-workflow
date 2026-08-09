// Vanilla client (GUI-SPEC §7.3, E5): native EventSource only, no framework, no
// external asset. New stream records are merged by integer seq (never rendered
// twice — not even an event appended between the snapshot and the stream start)
// and incorporated INTO the existing view: the phase bar, the trace tree and the
// per-node detail panes update live, without reloading, using the same data the
// server-rendered snapshot was built from. Problem messages append to the
// visible problem list. A later full snapshot renders identically.
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

  function esc(value) {
    return String(value == null ? "" : value);
  }

  function phaseCell(name) {
    return document.querySelector('.phase[data-name="' + name + '"]');
  }

  function setPhase(name, status) {
    var cell = phaseCell(name);
    if (cell) cell.className = "phase phase-" + status;
  }

  function nodeLabel(record) {
    var p = record.payload || {};
    if ((record.type === "phase" || record.type === "lane") && p.name) return p.name;
    if (record.type === "agent.run" && p.agent) return p.agent;
    if (record.type === "gate" && p.name) return p.name;
    if (record.type === "round") return "round " + p.n + "/" + p.cap;
    return record.type || "?";
  }

  function traceRoot() {
    var trace = document.querySelector(".trace");
    if (!trace) return null;
    var ul = trace.querySelector("ul");
    if (!ul) {
      ul = document.createElement("ul");
      trace.appendChild(ul);
    }
    return ul;
  }

  function childList(li) {
    var ul = li.querySelector(":scope > ul");
    if (!ul) {
      ul = document.createElement("ul");
      li.appendChild(ul);
    }
    return ul;
  }

  function ensureNode(record) {
    var span = record.span;
    if (!span) return null;
    var existing = document.querySelector('.node[data-span="' + span + '"]');
    if (existing) return existing;
    var li = document.createElement("li");
    li.className = "node node-running";
    li.setAttribute("data-span", span);
    li.setAttribute("data-seq", esc(record.seq));
    var label = document.createElement("span");
    label.className = "label";
    label.textContent = nodeLabel(record);
    li.appendChild(label);
    var parentLi = record.parent
      ? document.querySelector('.node[data-span="' + record.parent + '"]')
      : null;
    var target = parentLi ? childList(parentLi) : traceRoot();
    if (target) target.appendChild(li);
    ensurePane(record);
    return li;
  }

  function ensurePane(record) {
    var span = record.span;
    var panes = document.querySelector(".panes");
    if (!panes || !span) return null;
    var existing = panes.querySelector('.pane[data-span="' + span + '"]');
    if (existing) return existing;
    var pane = document.createElement("div");
    pane.className = "pane pane-" + String(record.type || "").replace(/\./g, "-");
    pane.setAttribute("data-span", span);
    var title = document.createElement("h3");
    title.textContent = nodeLabel(record);
    pane.appendChild(title);
    panes.appendChild(pane);
    return pane;
  }

  function appendToPane(record) {
    var pane = document.querySelector('.pane[data-span="' + record.span + '"]');
    if (!pane) return;
    var line = document.createElement("pre");
    line.className = "live-" + String(record.type || "").replace(/\./g, "-");
    line.textContent = JSON.stringify(record.payload);
    pane.appendChild(line);
  }

  function markEnded(record) {
    var li = document.querySelector('.node[data-span="' + record.span + '"]');
    if (li) li.className = "node node-done";
  }

  // Incorporate one accepted record into the live view (the same surfaces the
  // snapshot renders: phase bar, trace tree, detail panes).
  function applyEvent(record) {
    if (record.type === "phase") {
      var name = (record.payload || {}).name;
      if (name) setPhase(name, record.kind === "end" ? "completed" : "active");
    } else if (record.type === "escalation") {
      var failedPhase = (record.payload || {}).phase;
      if (failedPhase) setPhase(failedPhase, "failed");
    }
    if (record.kind === "start") {
      ensureNode(record);
    } else if (record.kind === "end") {
      markEnded(record);
    } else {
      appendToPane(record);
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
    applyEvent(record);
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
