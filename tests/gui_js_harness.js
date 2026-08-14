// Minimal, dependency-free JS harness for the behavioural GUI tests
// (.adw/plan.md §1, Aufgaben B and C). It executes the SERVED `app.js` in a plain
// `node` process inside hand-written stubs — DOM, fetch, performance (marks and
// measures), requestAnimationFrame and task scheduling — whose deferred responses
// and rAF/task queue the test drives deterministically. This is a development /
// test-time tool only (never a runtime dependency); it is NOT a browser and pulls
// in no npm package or browser-automation tool (no Playwright, no Selenium).
//
//   usage:  node gui_js_harness.js <app.js path> <scenario> [order]
//
// It prints exactly one JSON object on stdout with the observables the pytest
// side asserts, and nothing else; a failure prints to stderr and exits non-zero.
"use strict";

const fs = require("fs");

// ---------------------------------------------------------------------------
// A tiny DOM supporting only the operations app.js performs on the nodes the
// B/C scenarios build (querySelector/All with class/attr/descendant selectors,
// closest, classList.toggle, getAttribute/setAttribute, textContent, hidden).
// ---------------------------------------------------------------------------
class El {
  constructor(tag) {
    this.tag = String(tag).toUpperCase();
    this.tagName = this.tag;
    this.classes = new Set();
    this.attrs = {};
    this.children = [];
    this.parent = null;
    this._text = "";
    this.hidden = false;
    this.open = false;
    const self = this;
    this.classList = {
      toggle(c, on) {
        if (on === undefined) on = !self.classes.has(c);
        if (on) self.classes.add(c); else self.classes.delete(c);
        return on;
      },
      add(c) { self.classes.add(c); },
      remove(c) { self.classes.delete(c); },
      contains(c) { return self.classes.has(c); },
    };
  }
  get className() { return [...this.classes].join(" "); }
  setAttribute(k, v) {
    if (k === "class") this.classes = new Set(String(v).split(/\s+/).filter(Boolean));
    else this.attrs[k] = String(v);
  }
  getAttribute(k) {
    if (k === "class") return this.className;
    return (k in this.attrs) ? this.attrs[k] : null;
  }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this.children = []; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceWith(next) {
    const p = this.parent;
    if (!p) return;
    const i = p.children.indexOf(this);
    if (i !== -1) { p.children[i] = next; next.parent = p; this.parent = null; }
  }
  querySelector(sel) { return qsa(this, sel)[0] || null; }
  querySelectorAll(sel) { return qsa(this, sel); }
  closest(sel) {
    let n = this;
    while (n) { if (matchesCompound(n, sel)) return n; n = n.parent; }
    return null;
  }
  addEventListener() { /* elements need no listeners in these scenarios */ }
}

function el(tag, opts = {}) {
  const e = new El(tag);
  (opts.classes || []).forEach((c) => e.classes.add(c));
  Object.entries(opts.attrs || {}).forEach(([k, v]) => { e.attrs[k] = String(v); });
  if (opts.open) e.open = true;
  (opts.children || []).forEach((c) => e.appendChild(c));
  return e;
}

function parseSimple(part) {
  const s = { tag: null, classes: [], attrs: [] };
  const tagm = part.match(/^[a-zA-Z][\w-]*/);
  if (tagm) s.tag = tagm[0].toUpperCase();
  let m;
  const cre = /\.([\w-]+)/g;
  while ((m = cre.exec(part))) s.classes.push(m[1]);
  const are = /\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/g;
  while ((m = are.exec(part))) s.attrs.push({ name: m[1], value: m[2] === undefined ? null : m[2] });
  return s;
}
function matchesSimple(e, s) {
  if (!e || !(e instanceof El)) return false;
  if (s.tag && e.tag !== s.tag) return false;
  for (const c of s.classes) if (!e.classes.has(c)) return false;
  for (const a of s.attrs) {
    const v = e.getAttribute(a.name);
    if (v === null) return false;
    if (a.value !== null && v !== a.value) return false;
  }
  return true;
}
// Descendant combinator, right-to-left; sufficient for the <=2-part selectors used.
function matchesChain(e, parts) {
  const last = parts[parts.length - 1];
  if (!matchesSimple(e, last)) return false;
  let idx = parts.length - 2;
  let n = e.parent;
  while (idx >= 0) {
    let found = false;
    while (n) { if (matchesSimple(n, parts[idx])) { found = true; n = n.parent; break; } n = n.parent; }
    if (!found) return false;
    idx--;
  }
  return true;
}
function matchesCompound(e, sel) {
  return matchesChain(e, sel.trim().split(/\s+/).map(parseSimple));
}
function qsa(root, sel) {
  const parts = sel.trim().split(/\s+/).map(parseSimple);
  const res = [];
  (function walk(node) {
    for (const ch of node.children) {
      if (matchesChain(ch, parts)) res.push(ch);
      walk(ch);
    }
  })(root);
  return res;
}

// ---------------------------------------------------------------------------
// Controllable environment stubs.
// ---------------------------------------------------------------------------
const marks = [];
const measures = [];
let rafQ = [];
let timerQ = [];
const deferred = [];       // pending fetch() promises: {url, resolve, reject}
const listeners = {};      // document event listeners by type
const navigations = [];    // URLs passed to window.location.assign
let eventSource = null;    // the EventSource the client opens (to drive live refresh)
let nextParsedDoc = null;  // what DOMParser.parseFromString returns (the "fresh" swap DOM)

function installGlobals(rootDoc, body) {
  global.window = {
    location: { assign: function (url) { navigations.push(String(url)); } },
  };
  global.performance = {
    mark(name) { marks.push(name); },
    measure(name, start, end) { measures.push({ name, start, end }); },
    getEntriesByName(name) {
      return measures.filter((m) => m.name === name).map((m) => ({ name, duration: 1 }));
    },
    now() { return 0; },
  };
  global.requestAnimationFrame = (cb) => { rafQ.push(cb); return rafQ.length; };
  global.setTimeout = (cb) => { timerQ.push(cb); return timerQ.length; };
  global.fetch = (url) => new Promise((resolve, reject) => {
    deferred.push({ url: String(url), resolve, reject });
  });
  global.EventSource = function EventSource() {
    this.onmessage = null;
    this.addEventListener = function () {};
    this.close = function () {};
    eventSource = this;
  };
  global.DOMParser = function DOMParser() {
    this.parseFromString = function () {
      return nextParsedDoc || { querySelector: () => null, querySelectorAll: () => [] };
    };
  };
  global.document = {
    body,
    querySelector: (sel) => qsa(rootDoc, sel)[0] || null,
    querySelectorAll: (sel) => qsa(rootDoc, sel),
    addEventListener: (type, handler) => {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    createElement: (tag) => new El(tag),
  };
}

// deterministic drivers ------------------------------------------------------
const tick = () => new Promise((r) => process.nextTick(r));
async function drain() { for (let i = 0; i < 4; i++) await tick(); }
function flushRaf() { const q = rafQ; rafQ = []; q.forEach((cb) => cb()); }
function flushTimers() { const q = timerQ; timerQ = []; q.forEach((cb) => cb()); }
async function settle() {
  // Run several rounds so a promise-gated post-paint sequence (fetch settle ->
  // rAF -> task) — and any late/superseded work — fully drains.
  for (let i = 0; i < 4; i++) { await drain(); flushRaf(); await drain(); flushTimers(); await drain(); }
}
function resolveFetch(match, response) {
  const idx = deferred.findIndex((d) => d.url.indexOf(match) !== -1);
  if (idx === -1) throw new Error("no pending fetch matching " + match);
  const d = deferred.splice(idx, 1)[0];
  d.resolve(response);
}
function pendingFetchCount(match) {
  return deferred.filter((d) => d.url.indexOf(match) !== -1).length;
}
function resolveDetailFetch(html) {
  // The live-refresh GET of the detail page (path "/runs/...", not the "/api/..."
  // events route).
  const idx = deferred.findIndex(
    (d) => d.url.indexOf("/runs/") !== -1 && d.url.indexOf("/api/") === -1);
  if (idx === -1) throw new Error("no pending detail-page fetch");
  const d = deferred.splice(idx, 1)[0];
  d.resolve(textResponse(html));
}
function eventsResponse(records) {
  return { ok: true, status: 200, json: () => Promise.resolve(records) };
}
function textResponse(text) {
  return { ok: true, status: 200, text: () => Promise.resolve(text) };
}
function dispatch(type, event) { (listeners[type] || []).forEach((h) => h(event)); }
function countMeasure(name) { return measures.filter((m) => m.name === name).length; }
function countMark(name) { return marks.filter((m) => m === name).length; }

function loadAppJs(path) {
  // The served IIFE runs on evaluation: it reads document.body and wires the
  // delegated listeners we then dispatch to. Bare `document`, `performance`,
  // `fetch`, ... resolve to the globals installed above.
  // eslint-disable-next-line no-eval
  (0, eval)(fs.readFileSync(path, "utf8"));
}

// ---------------------------------------------------------------------------
// Scenario DOMs and steps.
// ---------------------------------------------------------------------------
function selectionDom() {
  // A dummy first node whose pane has NO lazy tool body, so the IIFE's initial
  // applySelection() selects it WITHOUT dispatching a fetch — then the scenario
  // drives the two real tool-node selections A (seq 10) and B (seq 20).
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const tree = el("div", { classes: ["trace"] });
  const panes = el("div", { classes: ["panes"] });
  const nodeDummy = el("div", { classes: ["node"], attrs: { "data-seq": "1" } });
  const nodeA = el("div", { classes: ["node"], attrs: { "data-seq": "10" } });
  const nodeB = el("div", { classes: ["node"], attrs: { "data-seq": "20" } });
  tree.append(nodeDummy, nodeA, nodeB);
  const paneDummy = el("div", { classes: ["pane"], attrs: { "data-seq": "1" } });
  const preA = el("pre", { attrs: { "data-load-seq": "10" } });
  const preB = el("pre", { attrs: { "data-load-seq": "20" } });
  const paneA = el("div", { classes: ["pane"], attrs: { "data-seq": "10" },
    children: [el("div", { classes: ["tool-detail"], children: [preA] })] });
  const paneB = el("div", { classes: ["pane"], attrs: { "data-seq": "20" },
    children: [el("div", { classes: ["tool-detail"], children: [preB] })] });
  panes.append(paneDummy, paneA, paneB);
  body.append(tree, panes);
  return { body, nodeA, nodeB, paneA, paneB, preA, preB };
}

async function runSupersession(order) {
  const dom = selectionDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  dispatch("click", { target: dom.nodeA }); await drain();  // select A, fetch(10)
  dispatch("click", { target: dom.nodeB }); await drain();  // select B, fetch(20)

  const [firstSeq, secondSeq] = order === "AB" ? ["10", "20"] : ["20", "10"];
  const payload = (seq) => [{ seq: Number(seq), payload: { marker: "CONTENT-" + seq } }];
  resolveFetch("from_seq=" + firstSeq + "&", eventsResponse(payload(firstSeq)));
  await settle();
  resolveFetch("from_seq=" + secondSeq + "&", eventsResponse(payload(secondSeq)));
  await settle();

  return {
    ok: true,
    order,
    measures_select: countMeasure("adw:select"),
    start_marks: countMark("adw:select:start"),
    end_marks: countMark("adw:select:end"),
    paneA_text: dom.preA.textContent,
    paneB_text: dom.preB.textContent,
    paneA_selected: dom.paneA.classes.has("selected"),
    paneB_selected: dom.paneB.classes.has("selected"),
  };
}

async function runReselectInflight() {
  // Double-click / A->B->A while A's FIRST tool-body fetch is still in flight. The
  // re-selection of A must (1) NOT complete the adw:select measure on the "Loading…"
  // placeholder, and (2) still render A's payload when the in-flight response
  // arrives — A is the last-chosen node, so its pane must never be cleared.
  const dom = selectionDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  const payload = (seq) => [{ seq: Number(seq), payload: { marker: "CONTENT-" + seq } }];

  dispatch("click", { target: dom.nodeA }); await drain();   // A, fetch A#1 (in flight)
  dispatch("click", { target: dom.nodeB }); await drain();   // B, fetch B (in flight)
  dispatch("click", { target: dom.nodeA }); await settle();  // re-select A: reuse in-flight A#1

  // A#1 has not returned yet: the measure must NOT have completed on "Loading…".
  const measure_before_resolve = countMeasure("adw:select");
  const paneA_before = dom.preA.textContent;

  resolveFetch("from_seq=20&", eventsResponse(payload("20"))); await settle();  // B superseded
  resolveFetch("from_seq=10&", eventsResponse(payload("10"))); await settle();  // A#1 -> render A

  return {
    ok: true,
    measure_before_resolve,
    paneA_before,
    measures_select: countMeasure("adw:select"),
    paneA_text: dom.preA.textContent,
    paneA_selected: dom.paneA.classes.has("selected"),
  };
}

async function runSupersessionDeferredClick() {
  // The P1 race: A settles and SCHEDULES its post-paint rAF/task WHILE it is still
  // current; only THEN is B selected; only THEN do the queued rAF/task callbacks
  // run. A's end mark + measure must still be suppressed (checked inside the task,
  // not just before scheduling), so only B is measured (B2).
  const dom = selectionDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  const payload = (seq) => [{ seq: Number(seq), payload: { marker: "CONTENT-" + seq } }];

  dispatch("click", { target: dom.nodeA }); await drain();       // select A, fetch(10)
  resolveFetch("from_seq=10&", eventsResponse(payload("10")));
  await drain();                                             // A settles -> rAF SCHEDULED (not run)
  dispatch("click", { target: dom.nodeB }); await drain();       // select B AFTER A scheduled its task
  resolveFetch("from_seq=20&", eventsResponse(payload("20")));
  await settle();                                            // now run every queued rAF/task

  return {
    ok: true,
    measures_select: countMeasure("adw:select"),
    start_marks: countMark("adw:select:start"),
    end_marks: countMark("adw:select:end"),
    paneB_text: dom.preB.textContent,
    paneB_selected: dom.paneB.classes.has("selected"),
  };
}

async function runSupersessionReselect() {
  // Regression (P2): after A is superseded and A's response arrives late, the stale
  // response is discarded — but the pane must be RESTORED to an unloaded state so
  // re-selecting A re-fetches and renders A's payload (not stuck at "Loading…"
  // forever until a page reload).
  const dom = selectionDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  const payload = (seq) => [{ seq: Number(seq), payload: { marker: "CONTENT-" + seq } }];

  dispatch("click", { target: dom.nodeA }); await drain();   // A, fetch(10) #1
  dispatch("click", { target: dom.nodeB }); await drain();   // B supersedes A, fetch(20)
  resolveFetch("from_seq=20&", eventsResponse(payload("20"))); await settle();  // B renders
  resolveFetch("from_seq=10&", eventsResponse(payload("10"))); await settle();  // A stale -> discarded

  // Re-select A: it must issue a NEW fetch (its loaded state was restored).
  dispatch("click", { target: dom.nodeA }); await drain();
  const refetched = pendingFetchCount("from_seq=10&") > 0;
  if (refetched) { resolveFetch("from_seq=10&", eventsResponse(payload("10"))); await settle(); }

  return {
    ok: true,
    refetched,
    paneA_text: dom.preA.textContent,
    paneA_selected: dom.paneA.classes.has("selected"),
  };
}

function detailRegion() {
  // A `main.detail` region with a dummy first node (so the initial/refresh
  // applySelection selects it without a fetch) and two tool nodes A (seq 10) and
  // B (seq 20) whose panes carry an UNLOADED lazy tool body.
  const nodeDummy = el("div", { classes: ["node"], attrs: { "data-seq": "1" } });
  const nodeA = el("div", { classes: ["node"], attrs: { "data-seq": "10" } });
  const nodeB = el("div", { classes: ["node"], attrs: { "data-seq": "20" } });
  const trace = el("div", { classes: ["trace"], children: [nodeDummy, nodeA, nodeB] });
  const preA = el("pre", { attrs: { "data-load-seq": "10" } });
  const preB = el("pre", { attrs: { "data-load-seq": "20" } });
  const paneDummy = el("div", { classes: ["pane"], attrs: { "data-seq": "1" } });
  const paneA = el("div", { classes: ["pane"], attrs: { "data-seq": "10" },
    children: [el("div", { classes: ["tool-detail"], children: [preA] })] });
  const paneB = el("div", { classes: ["pane"], attrs: { "data-seq": "20" },
    children: [el("div", { classes: ["tool-detail"], children: [preB] })] });
  const panes = el("div", { classes: ["panes"], children: [paneDummy, paneA, paneB] });
  const main = el("main", { classes: ["detail"], children: [trace, panes] });
  return { main, nodeA, nodeB, paneA, paneB, preA, preB };
}

function refreshDom() {
  // Current document: header + detail region. The "fresh" region (returned by the
  // stubbed DOMParser on refresh) has UNLOADED panes, so the swap's applySelection
  // re-fetches the selected node's tool body — exactly the refresh-triggered fetch.
  const cur = detailRegion();
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" },
    children: [el("header", { classes: ["run-header"] }), cur.main] });

  const fresh = detailRegion();
  const freshRoot = el("html", { children: [el("body", {
    children: [el("header", { classes: ["run-header"] }), fresh.main] })] });

  return { body, nodeA: cur.nodeA, fresh: freshRoot, freshNodeB: fresh.nodeB,
    freshPreA: fresh.preA, freshPreB: fresh.preB, freshPaneB: fresh.paneB };
}

async function runRefreshSupersession() {
  // P1: the live-refresh swap reapplies the selection and can start a tool-body
  // fetch. If that fetch is not tied to the current generation, a newer selection
  // cannot supersede it and its obsolete payload is written into the refreshed DOM.
  const dom = refreshDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  const payload = (seq) => [{ seq: Number(seq), payload: { marker: "CONTENT-" + seq } }];

  // Select A and load it, so A is the current selection when the refresh happens.
  dispatch("click", { target: dom.nodeA }); await drain();
  resolveFetch("from_seq=10&", eventsResponse(payload("10"))); await settle();

  // A live refresh: swapRegions replaces the region with the fresh (unloaded) DOM,
  // and its applySelection re-fetches A's tool body.
  nextParsedDoc = dom.fresh;
  eventSource.onmessage({ data: JSON.stringify({ type: "phase", kind: "point" }) });
  await drain(); flushTimers(); await drain();          // scheduleRefresh -> refresh() -> GET detail
  resolveDetailFetch("<html></html>"); await drain();   // -> swapRegions -> applySelection -> fetch A#2

  // A newer selection supersedes the refresh-triggered fetch.
  dispatch("click", { target: dom.freshNodeB }); await drain();
  resolveFetch("from_seq=20&", eventsResponse(payload("20"))); await settle();  // B renders

  // The refresh-triggered fetch for A resolves LAST — it must write nothing.
  resolveFetch("from_seq=10&", eventsResponse(payload("10"))); await settle();

  return {
    ok: true,
    freshPaneA_text: dom.freshPreA.textContent,
    freshPaneB_text: dom.freshPreB.textContent,
    freshPaneB_selected: dom.freshPaneB.classes.has("selected"),
  };
}

function timelineDom() {
  // A dummy in-window node/pane (so the initial applySelection selects it without a
  // fetch), an IN-window bar whose node HAS a pane (seq 10), and an OUT-of-window
  // bar whose node has NO pane (seq 99).
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const tree = el("div", { classes: ["trace"],
    children: [el("div", { classes: ["node"], attrs: { "data-seq": "1" } })] });
  const barIn = el("span", { classes: ["tl-bar"], attrs: { "data-seq": "10" } });
  const barOut = el("span", { classes: ["tl-bar"], attrs: { "data-seq": "99" } });
  const lanes = el("div", { classes: ["timeline-lanes"], children: [barIn, barOut] });
  const paneDummy = el("div", { classes: ["pane"], attrs: { "data-seq": "1" } });
  const pane10 = el("div", { classes: ["pane"], attrs: { "data-seq": "10" } });
  const panes = el("div", { classes: ["panes"], children: [paneDummy, pane10] });
  body.append(tree, lanes, panes);
  return { body, barIn, barOut, pane10 };
}

async function runTimelineFocus() {
  const dom = timelineDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  dispatch("click", { target: dom.barOut }); await drain();   // out-of-window: must navigate
  const navsAfterOut = navigations.slice();

  dispatch("click", { target: dom.barIn }); await settle();   // in-window: select in place
  return {
    ok: true,
    nav_out: navsAfterOut,
    nav_all: navigations.slice(),
    pane10_selected: dom.pane10.classes.has("selected"),
  };
}

function artifactDom() {
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  // A dummy node so the IIFE's initial applySelection() has something to select
  // and dispatches no fetch of its own.
  const tree = el("div", { classes: ["trace"],
    children: [el("div", { classes: ["node"], attrs: { "data-seq": "1" } })] });
  const panes = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "1" } })] });
  const pre = el("pre", { attrs: { "data-artifact-body": "" } });
  const more = el("a", { attrs: { "data-artifact-more": "", href: "#" } });
  more.hidden = true;
  const summary = el("summary", { classes: ["artifact-open"], attrs: { "data-artifact": "plan.md" } });
  const details = el("details", { classes: ["artifact-wrap"], children: [summary, pre, more] });
  body.append(tree, panes, details);
  return { body, details, summary, pre, more };
}

async function runArtifact() {
  const dom = artifactDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  const HEAD = "HUGEARTIFACTHEAD";
  const TAIL = "HUGEARTIFACTTAIL";
  const bigText = HEAD + "\n" + "x".repeat(200000) + "\n" + TAIL;

  dom.details.open = true;
  dispatch("toggle", { target: dom.details }); await drain();
  const afterDispatch = {
    measure: countMeasure("adw:artifact"),
    start: countMark("adw:artifact:start"),
    pre_text_len: dom.pre.textContent.length,
  };

  resolveFetch("/artifacts/", textResponse(bigText)); await drain();
  const afterResponse = {
    measure: countMeasure("adw:artifact"),
    has_head: dom.pre.textContent.indexOf(HEAD) !== -1,
    has_tail: dom.pre.textContent.indexOf(TAIL) !== -1,
    pre_text_len: dom.pre.textContent.length,
  };

  flushRaf(); await drain();
  const afterRaf = { measure: countMeasure("adw:artifact") };

  flushTimers(); await drain();
  const afterTimer = { measure: countMeasure("adw:artifact"), end: countMark("adw:artifact:end") };

  return { ok: true, afterDispatch, afterResponse, afterRaf, afterTimer };
}

// ---------------------------------------------------------------------------
const APP = process.argv[2];
const SCENARIO = process.argv[3];
const ARG = process.argv[4];

(async () => {
  let result;
  if (SCENARIO === "supersession") result = await runSupersession(ARG || "BA");
  else if (SCENARIO === "reselect-inflight") result = await runReselectInflight();
  else if (SCENARIO === "supersession-reselect") result = await runSupersessionReselect();
  else if (SCENARIO === "supersession-deferred") result = await runSupersessionDeferredClick();
  else if (SCENARIO === "refresh-supersession") result = await runRefreshSupersession();
  else if (SCENARIO === "timeline-focus") result = await runTimelineFocus();
  else if (SCENARIO === "artifact") result = await runArtifact();
  else throw new Error("unknown scenario: " + SCENARIO);
  process.stdout.write(JSON.stringify(result));
})().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
