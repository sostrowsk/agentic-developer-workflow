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
  // --- Brief 4 (keyboard operability): elements MAY carry their own listeners so
  // the keydown scenarios work whether app.js delegates on `document` or attaches a
  // handler to the tree/tab container. `focus()` records the active element and
  // `click()` counts any synthesised click so a test can prove selection happened
  // via the keyboard WITHOUT a synthetic click (AC 1).
  addEventListener(type, handler) {
    if (typeof handler !== "function") return;
    (this._listeners = this._listeners || {});
    (this._listeners[type] = this._listeners[type] || []).push(handler);
  }
  focus() { if (global.document) global.document.activeElement = this; }
  blur() { if (global.document && global.document.activeElement === this) global.document.activeElement = null; }
  // A programmatic click (as app.js uses to activate a focused row-action link from
  // the keyboard) is recorded AND dispatched through the delegated document listeners,
  // then — for an unprevented anchor — performs the anchor's navigation, mirroring a
  // real browser so a test can observe the action actually happening.
  click() {
    synthClicks.push(this);
    const self = this;
    const ev = { target: this, defaultPrevented: false,
      preventDefault() { ev.defaultPrevented = true; }, stopPropagation() {} };
    (listeners.click || []).forEach((h) => h(ev));
    if (!ev.defaultPrevented && self.tag === "A") {
      const href = self.getAttribute("href");
      if (href) navigations.push(href);
    }
  }
  scrollIntoView(opts) { scrollCalls.push({ el: this, opts: opts || null }); }
  get tabIndex() {
    var v = this.getAttribute("tabindex");
    return v === null ? -1 : parseInt(v, 10);
  }
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
const synthClicks = [];    // elements on which app.js called .click() (must stay empty, AC 1)
const scrollCalls = [];    // elements app.js scrolled into view (the cursor must stay visible)
let eventSource = null;    // the EventSource the client opens (to drive live refresh)
let nextParsedDoc = null;  // what DOMParser.parseFromString returns (the "fresh" swap DOM)

function installGlobals(rootDoc, body, search) {
  global.window = {
    location: {
      assign: function (url) { navigations.push(String(url)); },
      // The query string in effect for the page — it carries the paged window
      // (`offset`, `tools_offset`, `focus`). Empty unless a scenario sets one.
      search: search || "",
    },
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
    activeElement: body,
    querySelector: (sel) => qsa(rootDoc, sel)[0] || null,
    querySelectorAll: (sel) => qsa(rootDoc, sel),
    addEventListener: (type, handler) => {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    createElement: (tag) => new El(tag),
  };
}

// Brief 4: dispatch a `keydown` to app.js — whether it listens on `document`
// (delegation, as it does for click/toggle) or on the tree/tab container. The event
// bubbles from `target` up its ancestors, then to the document listeners; a
// `preventDefault()` sets `defaultPrevented` so a test can prove the space key does
// not scroll the page (AC 1). Ctrl/Alt/Meta chords are passed through so a test can
// assert they are NOT intercepted (spec key table).
function fireKey(target, key, mods) {
  mods = mods || {};
  const ev = {
    type: "keydown", key, target,
    ctrlKey: !!mods.ctrl, altKey: !!mods.alt, metaKey: !!mods.meta, shiftKey: !!mods.shift,
    defaultPrevented: false,
    preventDefault() { ev.defaultPrevented = true; },
    stopPropagation() {},
  };
  const chain = [];
  let n = target;
  while (n) { chain.push(n); n = n.parent; }
  chain.forEach((node) => (node._listeners && node._listeners.keydown || []).forEach((h) => h(ev)));
  (listeners.keydown || []).forEach((h) => h(ev));
  return ev;
}

// The `data-seq` of the currently selected node (the node carrying the `.selected`
// class app.js maintains), or null.
function selectedNodeSeq() {
  const n = global.document.querySelector(".node.selected");
  return n ? n.getAttribute("data-seq") : null;
}

// The data-seq values of the nodes the client has marked machine-readably selected
// (A5/AC 10) — beyond the `.selected` class — via `aria-selected="true"`.
function ariaSelectedSeqs() {
  return global.document.querySelectorAll('.node[aria-selected="true"]')
    .map((n) => n.getAttribute("data-seq")).sort();
}

// Count the elements in `root` that are a SEQUENTIAL tab stop (AC 4): a natively
// focusable tag not opted out with tabindex="-1", or any element with tabindex >= 0.
function sequentialTabStops(root) {
  const NATIVE = { A: 1, BUTTON: 1, INPUT: 1, SELECT: 1, TEXTAREA: 1, SUMMARY: 1 };
  let count = 0;
  (function walk(node) {
    node.children.forEach((c) => {
      const ti = c.getAttribute("tabindex");
      if (ti !== null) { if (parseInt(ti, 10) >= 0) count++; }
      else if (NATIVE[c.tag]) count++;
      walk(c);
    });
  })(root);
  return count;
}

// The data-tab a group's selected tab reports (aria-selected="true"), the count of
// such tabs, and the count of buttons that are a sequential tab stop (roving).
function tabGroupState(group) {
  let selected = null;
  let selectedCount = 0;
  let tabStops = 0;
  group.querySelectorAll(".tab-btn").forEach((b) => {
    if (b.closest("[data-tabs]") !== group) return;  // nested group: not ours
    if (b.getAttribute("aria-selected") === "true") { selectedCount++; if (!selected) selected = b.getAttribute("data-tab"); }
    const ti = b.getAttribute("tabindex");
    if (ti === null || parseInt(ti, 10) >= 0) tabStops++;
  });
  return { selected, selectedCount, tabStops };
}

function activePanelName(group) {
  let name = null;
  group.querySelectorAll("[data-tab-panel]").forEach((p) => {
    if (p.closest("[data-tabs]") === group && p.classList.contains("active") && !name) {
      name = p.getAttribute("data-tab-panel");
    }
  });
  return name;
}

function activeButtonTab(group) {
  let name = null;
  group.querySelectorAll(".tab-btn").forEach((b) => {
    if (b.closest("[data-tabs]") === group && b.classList.contains("active") && !name) {
      name = b.getAttribute("data-tab");
    }
  });
  return name;
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

function openStateRegion() {
  // A `main.detail` region holding an expandable Tools entry (<details> with a lazy
  // pre[data-load-seq]) plus a dummy node/pane so applySelection has a target.
  const pre = el("pre", { attrs: { "data-load-seq": "5" } });
  const details = el("details", { children: [el("summary"), pre] });  // closed by default
  const ul = el("ul", { classes: ["tool-list"],
    children: [el("li", { classes: ["tool-agent-tool-call"], children: [details] })] });
  const trace = el("div", { classes: ["trace"],
    children: [el("div", { classes: ["node"], attrs: { "data-seq": "1" } })] });
  const panes = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "1" } })] });
  const main = el("main", { classes: ["detail"], children: [trace, ul, panes] });
  return { main, details };
}

async function runOpenStateSwap() {
  // The live region swap must preserve the user's expand choice for the <details>
  // that still exist (Tools entries / Raw rows / artifact wraps) — the flat trace
  // tree is not collapsible, so the old tree-scoped capture was a dead no-op.
  const cur = openStateRegion();
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" },
    children: [el("header", { classes: ["run-header"] }), cur.main] });
  installGlobals(el("html", { children: [body] }), body);
  loadAppJs(APP);

  cur.details.open = true;  // the user expands the Tools entry

  const fresh = openStateRegion();  // the fresh swap DOM renders it CLOSED (server default)
  const freshRoot = el("html", { children: [el("body", {
    children: [el("header", { classes: ["run-header"] }), fresh.main] })] });
  nextParsedDoc = freshRoot;

  eventSource.onmessage({ data: JSON.stringify({ type: "phase", kind: "point" }) });
  await drain(); flushTimers(); await drain();
  resolveDetailFetch("<html></html>"); await drain();

  return { ok: true, fresh_details_open: fresh.details.open };
}

async function runRefreshWindow(search) {
  // The live-refresh GET must carry the query string in effect, so the swapped-in
  // markup is the SAME window the user is looking at. Without it the server renders
  // its default (first) window and the wholesale swap silently discards the user's
  // paged position — the moving window is what makes the bounded DOM reachable.
  const cur = openStateRegion();
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" },
    children: [el("header", { classes: ["run-header"] }), cur.main] });
  installGlobals(el("html", { children: [body] }), body, search);
  loadAppJs(APP);

  eventSource.onmessage({ data: JSON.stringify({ type: "phase", kind: "point" }) });
  await drain(); flushTimers(); await drain();

  // Capture the pending detail-page GET without resolving it.
  const pending = deferred.filter(
    (d) => d.url.indexOf("/runs/") !== -1 && d.url.indexOf("/api/") === -1);
  const url = pending.length ? pending[0].url : null;
  resolveDetailFetch("<html></html>"); await drain();

  return { ok: true, refresh_url: url, refresh_fetch_count: pending.length };
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

function lazyPaneDom() {
  // The unpaged tree column: a SPAN node with its own pane (seq 1) and a POINT node
  // that has a tree row but NO pane of its own (seq 42) — the case the shared
  // generic pane shell serves.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const spanRow = el("li", { classes: ["node"], attrs: { "data-seq": "1" } });
  const pointLabel = el("span", { classes: ["label"] });
  pointLabel.textContent = "Read adw/gui/app.py";
  const pointRow = el("li", { classes: ["node"],
    attrs: { "data-seq": "42", "data-node-type": "agent.tool.call" },
    children: [pointLabel] });
  const pointLabel2 = el("span", { classes: ["label"] });
  pointLabel2.textContent = "Grep needle";
  const pointRow2 = el("li", { classes: ["node"],
    attrs: { "data-seq": "43", "data-node-type": "agent.tool.call" },
    children: [pointLabel2] });
  const tree = el("div", { classes: ["trace"], children: [spanRow, pointRow, pointRow2] });
  const spanPane = el("div", { classes: ["pane"], attrs: { "data-seq": "1" } });
  const genLabel = el("span", { attrs: { "data-generic-label": "" } });
  const genType = el("small", { classes: ["type"], attrs: { "data-generic-type": "" } });
  const genBody = el("pre", { classes: ["raw", "tool-body"], attrs: { "data-load-seq": "" } });
  const generic = el("div", { classes: ["pane", "pane-generic"],
    attrs: { "data-generic-pane": "" },
    children: [el("h3", { children: [genLabel, genType] }), genBody] });
  const panes = el("div", { classes: ["panes"], children: [spanPane, generic] });
  body.append(tree, panes);
  return { body, spanRow, pointRow, pointRow2, spanPane, generic, genLabel, genType, genBody };
}

async function runLazyPane() {
  const dom = lazyPaneDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  dispatch("click", { target: dom.pointRow }); await drain();
  const fetchedOwnRecord = pendingFetchCount("from_seq=42&to_seq=42") === 1;
  resolveFetch("from_seq=42", eventsResponse(
    [{ seq: 42, type: "agent.tool.call", payload: { tool: "Read", marker: "LAZYMARK" } }]));
  await settle();

  const afterPoint = {
    generic_selected: dom.generic.classes.has("selected"),
    span_selected: dom.spanPane.classes.has("selected"),
    load_seq: dom.genBody.getAttribute("data-load-seq"),
    label: dom.genLabel.textContent,
    type: dom.genType.textContent,
    body: dom.genBody.textContent,
    fetched_own_record: fetchedOwnRecord,
    navigations: navigations.slice(),
  };

  // Selecting the span node again must hand the pane back to its OWN pane.
  dispatch("click", { target: dom.spanRow }); await settle();
  return {
    ok: true,
    after_point: afterPoint,
    span_selected_after: dom.spanPane.classes.has("selected"),
    generic_selected_after: dom.generic.classes.has("selected"),
  };
}

async function runLazyPaneRace() {
  const dom = lazyPaneDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  // A then B, both before either fetch settles: the shared <pre> is re-pointed at B
  // while A's request is still in flight.
  dispatch("click", { target: dom.pointRow }); await drain();
  dispatch("click", { target: dom.pointRow2 }); await drain();

  // B (the node actually selected) answers FIRST and renders ...
  resolveFetch("from_seq=43", eventsResponse(
    [{ seq: 43, type: "agent.tool.call", payload: { marker: "FRESH_B" } }]));
  await drain();
  const afterFresh = { body: dom.genBody.textContent };

  // ... then A's response arrives LATE. The element no longer shows node A, so this
  // must neither write into it nor clear it.
  resolveFetch("from_seq=42", eventsResponse(
    [{ seq: 42, type: "agent.tool.call", payload: { marker: "STALE_A" } }]));
  await settle();

  return {
    ok: true,
    after_fresh: afterFresh,
    final_body: dom.genBody.textContent,
    final_load_seq: dom.genBody.getAttribute("data-load-seq"),
    generic_selected: dom.generic.classes.has("selected"),
  };
}

async function runPrettyPayload(payloadJson) {
  // The formatter is a closure inside the served IIFE; exercise it through the one
  // public path that uses it — the shared pane's lazy load.
  const dom = lazyPaneDom();
  const rootDoc = el("html", { children: [dom.body] });
  installGlobals(rootDoc, dom.body);
  loadAppJs(APP);

  dispatch("click", { target: dom.pointRow }); await drain();
  resolveFetch("from_seq=42", eventsResponse(
    [{ seq: 42, type: "agent.tool.call", payload: JSON.parse(payloadJson) }]));
  await settle();
  return { ok: true, text: dom.genBody.textContent };
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

// The read-only run-context panel (node-time run state). The panel is a fixed
// six-field list keyed by [data-context-field]; the six-field context of every
// node travels in the render as the node's `data-context` (JSON), and the
// no-selection fallback is the body's `data-latest-context` (JSON). Selecting a
// node must project THAT node's context onto the panel fields (time travel);
// a null field renders empty (never "0"). The panel starts unpopulated so the
// scenario proves the CLIENT projection, not a server pre-render.
function contextPanelDom() {
  var latest = { phase: "ci", round: null, limit_hits: 3, circuit_breakers: 1,
    cost_usd: 1.5, followups: 2 };
  var ctxA = { phase: "build", round: { loop: "gates", n: 2, cap: 5 },
    limit_hits: 1, circuit_breakers: null, cost_usd: 0.4, followups: null };
  var ctxB = { phase: "plan", round: null, limit_hits: null,
    circuit_breakers: null, cost_usd: null, followups: null };
  var body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111",
    "data-latest-context": JSON.stringify(latest) } });
  var nodeA = el("div", { classes: ["node"],
    attrs: { "data-seq": "10", "data-context": JSON.stringify(ctxA) } });
  var nodeB = el("div", { classes: ["node"],
    attrs: { "data-seq": "20", "data-context": JSON.stringify(ctxB) } });
  var trace = el("div", { classes: ["trace"], children: [nodeA, nodeB] });
  var paneA = el("div", { classes: ["pane"], attrs: { "data-seq": "10" } });
  var paneB = el("div", { classes: ["pane"], attrs: { "data-seq": "20" } });
  var panes = el("div", { classes: ["panes"], children: [paneA, paneB] });
  var fields = ["phase", "round", "limit_hits", "circuit_breakers", "cost_usd", "followups"]
    .map(function (f) {
      return el("span", { classes: ["ctx-value"], attrs: { "data-context-field": f } });
    });
  var panel = el("aside", { classes: ["run-context"], children: fields });
  body.append(trace, panes, panel);
  return { body: body, nodeA: nodeA, nodeB: nodeB };
}

async function runContextPanel() {
  var dom = contextPanelDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);

  function readPanel() {
    var out = {};
    ["phase", "limit_hits", "circuit_breakers", "cost_usd", "followups"].forEach(function (f) {
      var e = document.querySelector('[data-context-field="' + f + '"]');
      out[f] = e ? e.textContent : null;
    });
    return out;
  }

  dispatch("click", { target: dom.nodeA }); await settle();
  var afterA = readPanel();
  dispatch("click", { target: dom.nodeB }); await settle();  // time travel to B
  var afterB = readPanel();

  return { ok: true, afterA: afterA, afterB: afterB };
}

// Cost-format parity (AC 11): a single node whose context carries a given
// `cost_usd`; selecting it must project the cost onto the panel in the SAME format
// the server renders. The scenario returns the projected cell text for the value
// passed on argv, so the pytest side can compare it to Python's _fmt_cost across
// rounding boundaries (0.125, 2.675, …).
async function runCostFormat(arg) {
  var cost = parseFloat(arg);
  var ctx = { phase: null, round: null, limit_hits: null,
    circuit_breakers: null, cost_usd: cost, followups: null };
  var body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111",
    "data-latest-context": JSON.stringify({ phase: null, round: null, limit_hits: null,
      circuit_breakers: null, cost_usd: null, followups: null }) } });
  var node = el("div", { classes: ["node"],
    attrs: { "data-seq": "1", "data-context": JSON.stringify(ctx) } });
  var trace = el("div", { classes: ["trace"], children: [node] });
  var pane = el("div", { classes: ["pane"], attrs: { "data-seq": "1" } });
  var panes = el("div", { classes: ["panes"], children: [pane] });
  var field = el("span", { classes: ["ctx-value"],
    attrs: { "data-context-field": "cost_usd" } });
  var panel = el("aside", { classes: ["run-context"], children: [field] });
  body.append(trace, panes, panel);
  installGlobals(el("html", { children: [body] }), body);
  loadAppJs(APP);

  dispatch("click", { target: node }); await settle();
  return { ok: true, cost_usd: field.textContent };
}

function contextSwapMain() {
  // A `main.detail` region with a dummy node/pane (so applySelection selects it
  // without a fetch) and the run-context panel (empty value slots).
  var node = el("div", { classes: ["node"], attrs: { "data-seq": "1" } });
  var trace = el("div", { classes: ["trace"], children: [node] });
  var pane = el("div", { classes: ["pane"], attrs: { "data-seq": "1" } });
  var panes = el("div", { classes: ["panes"], children: [pane] });
  var fields = ["phase", "round", "limit_hits", "circuit_breakers", "cost_usd", "followups"]
    .map(function (f) {
      return el("span", { classes: ["ctx-value"], attrs: { "data-context-field": f } });
    });
  var panel = el("aside", { classes: ["run-context"], children: fields });
  var layout = el("div", { classes: ["trace-layout"], children: [trace, panes, panel] });
  return el("main", { classes: ["detail"], children: [layout] });
}

async function runContextLiveSwap() {
  // P1: a live-region swap must refresh the UNSELECTED context panel. The panel's
  // no-selection fallback (`data-latest-context`) lives on <body>, which is not one
  // of the swapped regions, so the swap must copy it from the fetched document —
  // otherwise the panel freezes at the value the page opened with.
  var L1 = { phase: "spec", round: null, limit_hits: null, circuit_breakers: null,
    cost_usd: null, followups: null };
  var L2 = { phase: "build", round: null, limit_hits: 2, circuit_breakers: null,
    cost_usd: null, followups: null };

  var body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111",
    "data-latest-context": JSON.stringify(L1) },
    children: [el("header", { classes: ["run-header"] }), contextSwapMain()] });
  installGlobals(el("html", { children: [body] }), body);
  loadAppJs(APP);

  function readField(f) {
    var e = document.querySelector('[data-context-field="' + f + '"]');
    return e ? e.textContent : null;
  }
  var before = { phase: readField("phase"), limit_hits: readField("limit_hits") };

  // The fetched refresh document carries an UPDATED latest_context on its body.
  var freshRoot = el("html", { children: [el("body", {
    attrs: { "data-latest-context": JSON.stringify(L2) },
    children: [el("header", { classes: ["run-header"] }), contextSwapMain()] })] });
  nextParsedDoc = freshRoot;

  eventSource.onmessage({ data: JSON.stringify({ type: "phase", kind: "point" }) });
  await drain(); flushTimers(); await drain();          // scheduleRefresh -> refresh -> GET detail
  resolveDetailFetch("<html></html>"); await drain();   // -> swapRegions -> updateContextPanel

  var after = { phase: readField("phase"), limit_hits: readField("limit_hits") };
  return { ok: true, before: before, after: after };
}

function recoveryLinkDom() {
  // A run-level tab group (trace active, artifacts inactive) whose Artifacts panel
  // holds the escalation.md entry as a closed <details>, plus the recovery card
  // whose report link targets it. A dummy node/pane lets the initial applySelection
  // select without a fetch.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const btnTrace = el("button", { classes: ["tab-btn", "active"], attrs: { "data-tab": "trace" } });
  const btnArt = el("button", { classes: ["tab-btn"], attrs: { "data-tab": "artifacts" } });
  const buttons = el("div", { classes: ["tab-buttons"], children: [btnTrace, btnArt] });
  const tracePanel = el("section", { classes: ["tab", "tab-trace", "active"],
    attrs: { "data-tab-panel": "trace" } });
  const pre = el("pre", { attrs: { "data-artifact-body": "" } });
  const more = el("a", { attrs: { "data-artifact-more": "", href: "#" } });
  more.hidden = true;
  const summary = el("summary", { classes: ["artifact-open"],
    attrs: { "data-artifact": "escalation.md" } });
  const details = el("details", { classes: ["artifact-wrap"], children: [summary, pre, more] });
  const artPanel = el("section", { classes: ["tab", "tab-artifacts"],
    attrs: { "data-tab-panel": "artifacts" }, children: [details] });
  const tabs = el("div", { classes: ["tabs", "run-tabs"], attrs: { "data-tabs": "" },
    children: [buttons, tracePanel, artPanel] });
  const link = el("a", { classes: ["recovery-artifact"],
    attrs: { "data-recovery-artifact": "escalation.md", href: "#" } });
  const card = el("section", { classes: ["recovery-card"],
    attrs: { "data-recovery-card": "", "data-recovery-kind": "none" }, children: [link] });
  const trace = el("div", { classes: ["trace"],
    children: [el("div", { classes: ["node"], attrs: { "data-seq": "1" } })] });
  const panes = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "1" } })] });
  const main = el("main", { classes: ["detail"], children: [tabs, trace, panes] });
  body.append(card, main);
  return { body, link, artPanel, tracePanel, details };
}

async function runRecoveryLink() {
  const dom = recoveryLinkDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);

  dispatch("click", { target: dom.link, preventDefault: function () {} }); await drain();

  // In a browser, opening the <details> fires a toggle -> loadArtifact. Mirror that
  // here to prove the revealed entry actually loads its content on demand.
  let fetch_escalation = 0;
  if (dom.details.open) {
    dispatch("toggle", { target: dom.details }); await drain();
    fetch_escalation = pendingFetchCount("/artifacts/escalation.md");
  }

  return {
    ok: true,
    navigations: navigations.slice(),          // no navigation: read-only link
    artifacts_active: dom.artPanel.classes.has("active"),
    trace_active: dom.tracePanel.classes.has("active"),
    details_open: dom.details.open,
    fetch_escalation: fetch_escalation,
  };
}

function recoveryLiveMain(withCard, kind) {
  // A `main.detail` region (a live-refresh REGION) with a dummy node/pane so the
  // initial/refresh applySelection selects without a fetch, optionally carrying the
  // run-level recovery card as its first child.
  var children = [];
  if (withCard) {
    var cmd = el("code", { classes: ["recovery-command"] });
    children.push(el("section", { classes: ["recovery-card"],
      attrs: { "data-recovery-card": "", "data-recovery-kind": kind || "approve" },
      children: [cmd] }));
  }
  var trace = el("div", { classes: ["trace"],
    children: [el("div", { classes: ["node"], attrs: { "data-seq": "1" } })] });
  var panes = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "1" } })] });
  children.push(trace, panes);
  return el("main", { classes: ["detail"], children: children });
}

function recoveryLiveBody(withCard, kind) {
  return el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" },
    children: [el("header", { classes: ["run-header"] }), recoveryLiveMain(withCard, kind)] });
}

async function refreshWith(freshBody) {
  // Drive one SSE-triggered live refresh whose fetched document is `freshBody`.
  nextParsedDoc = el("html", { children: [freshBody] });
  eventSource.onmessage({ data: JSON.stringify({ type: "phase", kind: "point" }) });
  await drain(); flushTimers(); await drain();          // scheduleRefresh -> refresh -> GET detail
  resolveDetailFetch("<html></html>"); await drain();   // -> swapRegions
}

async function runRecoveryLive() {
  // The run-level recovery card must live inside a swapped region so a live refresh
  // ADDS it when the run needs a human step and REMOVES it when it no longer does.
  var body = recoveryLiveBody(false);
  installGlobals(el("html", { children: [body] }), body);
  loadAppJs(APP);

  var cardKind = function () {
    var c = document.querySelector("[data-recovery-card]");
    return c ? c.getAttribute("data-recovery-kind") : null;
  };

  var initial = cardKind();                              // running: no card
  await refreshWith(recoveryLiveBody(true, "approve"));  // enters an approval gate
  var afterApprove = cardKind();
  await refreshWith(recoveryLiveBody(true, "resume"));   // aborts -> resumable
  var afterResume = cardKind();
  await refreshWith(recoveryLiveBody(false));            // moves on: card removed
  var afterClear = cardKind();

  return { ok: true, initial: initial, afterApprove: afterApprove,
    afterResume: afterResume, afterClear: afterClear };
}

function traceFocusFoldDom() {
  // A trace-list with a default-open phase (seq 2) and a NON-default target phase
  // (seq 8) whose subtree is a group wrapper holding a repetition wrapper holding the
  // ?focus target call (seq 99), nested two <details> deep. On load the target phase
  // is collapsed by default; ?focus=99 must open the phase AND both enclosing
  // <details> so the deep call is revealed (A5). Depth rides in the inline style, as
  // the server renders it.
  var body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111",
    "data-focus": "99" } });
  var phaseDefault = el("li", { classes: ["node"],
    attrs: { "data-seq": "2", "data-node-type": "phase", "style": "--depth:1" } });
  var phaseTarget = el("li", { classes: ["node"],
    attrs: { "data-seq": "8", "data-node-type": "phase", "style": "--depth:1" } });
  var focusedCall = el("li", { classes: ["node"],
    attrs: { "data-seq": "99", "data-node-type": "agent.tool.call", "style": "--depth:3" } });
  var repeatDetails = el("details", { classes: ["trace-collapse"], children: [
    el("summary", { classes: ["trace-summary"] }),
    el("ul", { classes: ["trace-sublist"], children: [focusedCall] })] });
  var repeatLi = el("li", { classes: ["trace-wrap", "trace-repeat"],
    attrs: { "style": "--depth:3" }, children: [repeatDetails] });
  var groupDetails = el("details", { classes: ["trace-collapse"], children: [
    el("summary", { classes: ["trace-summary"] }),
    el("ul", { classes: ["trace-sublist"], children: [repeatLi] })] });
  var groupLi = el("li", { classes: ["trace-wrap", "trace-group"],
    attrs: { "style": "--depth:3" }, children: [groupDetails] });
  var list = el("ul", { classes: ["trace-list"], attrs: { "data-default-phase": "2" },
    children: [phaseDefault, phaseTarget, groupLi] });
  var trace = el("div", { classes: ["trace"], children: [list] });
  var panes = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "99" } })] });
  body.append(trace, panes);
  return { body: body, phaseDefault: phaseDefault, phaseTarget: phaseTarget,
    groupLi: groupLi, groupDetails: groupDetails, repeatDetails: repeatDetails,
    focusedCall: focusedCall };
}

async function runTraceFocusFold() {
  var dom = traceFocusFoldDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();  // the IIFE runs initTreeFold() on load

  return {
    ok: true,
    default_phase_open: dom.phaseDefault.classes.has("phase-open"),
    target_phase_open: dom.phaseTarget.classes.has("phase-open"),
    group_row_hidden: dom.groupLi.classes.has("fold-hidden"),
    group_open: dom.groupDetails.open,
    repeat_open: dom.repeatDetails.open,
    focused_hidden: dom.focusedCall.classes.has("fold-hidden"),
  };
}

// ---------------------------------------------------------------------------
// Brief 4 scenarios: keyboard operability of the trace tree, the timeline row, the
// tab pattern, and the machine-readable selection. All observed through the served
// app.js — the harness only dispatches keydown/click and reads the resulting DOM
// state (selection class, aria-*, tabindex, navigations); it is not a browser and
// simulates no layout.
// ---------------------------------------------------------------------------

function treeNode(seq, depth, type) {
  return el("li", { classes: ["node"],
    attrs: { "data-seq": String(seq), "data-node-type": type || "agent.tool.call",
      "style": "--depth:" + depth },
    children: [el("span", { classes: ["label"] })] });
}
function treePhase(seq, depth) {
  return el("li", { classes: ["node"],
    attrs: { "data-seq": String(seq), "data-node-type": "phase", "style": "--depth:" + depth },
    children: [
      el("button", { classes: ["fold-toggle"], attrs: { "data-fold-toggle": "", "aria-expanded": "true" } }),
      el("span", { classes: ["label"] })] });
}
function treePane(seq) { return el("div", { classes: ["pane"], attrs: { "data-seq": String(seq) } }); }

function keyboardTreeDom() {
  // A `.trace-list` with a dummy first node (seq 1, its own pane so the initial
  // applySelection selects it WITHOUT a fetch), a default-open phase (seq 2) holding
  // one visible child (seq 3), a collapsed phase (seq 8) holding one hidden child
  // (seq 9) and a trailing plain node (seq 20). Depth rides in the inline style as
  // the server renders it.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const n1 = treeNode(1, 1);
  const p2 = treePhase(2, 1);
  const n3 = treeNode(3, 2);
  const p8 = treePhase(8, 1);
  // The hidden child (inside the collapsed phase 8) carries a raw-jump ACTION link —
  // a native <a href> that, once the fold opens, must not become a stray tab stop.
  const n9RawJump = el("a", { classes: ["raw-jump"], attrs: { href: "?raw_from_seq=9&raw_to_seq=9" } });
  const n9 = el("li", { classes: ["node"],
    attrs: { "data-seq": "9", "data-node-type": "agent.tool.call", "style": "--depth:2" },
    children: [el("span", { classes: ["label"] }), n9RawJump] });
  const n20 = treeNode(20, 1);
  const list = el("ul", { classes: ["trace-list"], attrs: { "data-default-phase": "2" },
    children: [n1, p2, n3, p8, n9, n20] });
  const trace = el("div", { classes: ["trace"], children: [list] });
  const panes = el("div", { classes: ["panes"],
    children: [treePane(1), treePane(3), treePane(9), treePane(20)] });
  body.append(trace, panes);
  return { body, trace, list, n1, p2, n3, p8, n9, n9RawJump, n20,
    pane3: panes.children[1], pane9: panes.children[2], pane20: panes.children[3] };
}

async function runTreeKeyboard() {
  const dom = keyboardTreeDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();  // initial applySelection selects the dummy first node (seq 1)

  const initial = selectedNodeSeq();
  // Roles: the column is a role="tree" whose rows are role="treeitem", so aria-selected
  // on a row is supported (a bare <li> would not support it).
  const roles = { list: dom.list.getAttribute("role"), item: dom.n3.getAttribute("role") };

  // Pure Down/Down moves the navigation cursor but must NOT select (AC 1). The move
  // manages real focus onto the cursor row (roving tabindex) and scrolls it into view,
  // so it stays visible in a tall tree.
  const scrollBefore = scrollCalls.length;
  fireKey(dom.list, "ArrowDown");  // seq 1 -> phase 2
  fireKey(dom.list, "ArrowDown");  // phase 2 -> visible child seq 3
  const afterMotion = selectedNodeSeq();
  const cursorEl = global.document.querySelector(".trace-list .tree-cursor");
  const cursor = {
    scrolled: scrollCalls.length > scrollBefore,
    focused: !!(cursorEl && global.document.activeElement === cursorEl),
    tabindex: cursorEl ? cursorEl.getAttribute("tabindex") : null,
  };

  // Enter selects the cursored node exactly like a click — same pane, no synth click.
  fireKey(dom.list, "Enter");
  await settle();
  const selEl = global.document.querySelector('.node[aria-selected="true"]');
  const afterEnter = {
    sel: selectedNodeSeq(),
    pane3_selected: dom.pane3.classes.has("selected"),
    measures: countMeasure("adw:select"),
    synth_clicks: synthClicks.length,
    selected_role: selEl ? selEl.getAttribute("role") : null,
  };

  // From seq 3: Down -> collapsed phase 8, Down -> seq 20 (the hidden child seq 9 is
  // SKIPPED, AC 2). Space selects and must prevent the page scroll (AC 1).
  fireKey(dom.list, "ArrowDown");  // seq 3 -> phase 8
  fireKey(dom.list, "ArrowDown");  // phase 8 -> seq 20 (skips hidden seq 9)
  const space = fireKey(dom.list, " ");
  await settle();
  const afterSpace = { sel: selectedNodeSeq(), space_default_prevented: space.defaultPrevented };

  // Home / End jump to the first / last VISIBLE row (AC 2 key table).
  fireKey(dom.list, "End"); fireKey(dom.list, "Enter"); await settle();
  const afterEnd = selectedNodeSeq();
  fireKey(dom.list, "Home"); fireKey(dom.list, "Enter"); await settle();
  const afterHome = selectedNodeSeq();

  return { ok: true, initial, roles, cursor,
    afterMotion, afterEnter, afterSpace, afterEnd, afterHome };
}

async function runTreeFoldKeys() {
  const dom = keyboardTreeDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();

  // Move the cursor onto the collapsed phase (seq 8): Down -> phase 2, Down -> seq 3,
  // Down -> phase 8.
  fireKey(dom.list, "ArrowDown");
  fireKey(dom.list, "ArrowDown");
  fireKey(dom.list, "ArrowDown");
  const start = { phase_open: dom.p8.classes.has("phase-open"), child_hidden: dom.n9.classes.has("fold-hidden") };

  // A Ctrl chord must NOT be intercepted (no fold change).
  fireKey(dom.list, "ArrowRight", { ctrl: true });
  const afterCtrlRight = { phase_open: dom.p8.classes.has("phase-open") };

  fireKey(dom.list, "ArrowRight");  // opens the collapsed phase (keyboard)
  const afterRight = { phase_open: dom.p8.classes.has("phase-open"), child_hidden: dom.n9.classes.has("fold-hidden") };
  // E2/AC 4: opening the fold must NOT create an extra sequential tab stop from the
  // revealed row's action link — checked BEFORE any further cursor move.
  const tab_stops_after_kbd_open = sequentialTabStops(dom.trace);

  fireKey(dom.list, "ArrowLeft");   // closes it again
  const afterLeft = { phase_open: dom.p8.classes.has("phase-open"), child_hidden: dom.n9.classes.has("fold-hidden") };

  // The same must hold when the fold is opened by the MOUSE phase caret (no arrow-key
  // move follows to re-normalise the roving tabindex).
  const caret = dom.p8.querySelector("[data-fold-toggle]");
  dispatch("click", { target: caret });
  const tab_stops_after_mouse_open = sequentialTabStops(dom.trace);
  dispatch("click", { target: caret });  // close again for the leaf check

  // On a leaf row (End -> seq 20) Right/Left change nothing and raise no error.
  fireKey(dom.list, "End");
  let leafError = false;
  try { fireKey(dom.list, "ArrowRight"); fireKey(dom.list, "ArrowLeft"); } catch (e) { leafError = true; }

  return { ok: true, start, afterCtrlRight, afterRight, afterLeft,
    tab_stops_after_kbd_open, tab_stops_after_mouse_open, leaf_no_error: !leafError };
}

function keyboardGroupDom() {
  // A `.trace-list` with a dummy node (seq 1), a default-open phase (seq 2, so a fold
  // <button> is present) and TWO collapsible wrappers — a group and a repetition,
  // each a <summary> plus a closed <details.trace-collapse> holding one child node
  // (seq 30 / seq 31). The wrappers carry no data-seq (not selectable) but ARE fold
  // rows: the keyboard must navigate to them and open them to reach their children.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  function wrapper(cls, child) {
    const summary = el("summary", { classes: ["trace-summary"] });
    const details = el("details", { classes: ["trace-collapse"],
      children: [summary, el("ul", { classes: ["trace-sublist"], children: [child] })] });
    const li = el("li", { classes: ["trace-wrap", cls], attrs: { "style": "--depth:1" },
      children: [details] });
    return { li, details, summary };
  }
  const n1 = treeNode(1, 1);
  const p2 = treePhase(2, 1);
  const gChild = treeNode(30, 2);
  const g = wrapper("trace-group", gChild);
  const rChild = treeNode(31, 2);
  const r = wrapper("trace-repeat", rChild);
  const list = el("ul", { classes: ["trace-list"], attrs: { "data-default-phase": "2" },
    children: [n1, p2, g.li, r.li] });
  const trace = el("div", { classes: ["trace"], children: [list] });
  const panes = el("div", { classes: ["panes"],
    children: [treePane(1), treePane(30), treePane(31)] });
  body.append(trace, panes);
  return { body, trace, list, group: g.li, groupDetails: g.details,
    repeat: r.li, repeatDetails: r.details, gChild, rChild };
}

async function runTreeTabStop() {
  const dom = keyboardGroupDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();
  // After client init the whole tree column is exactly ONE sequential tab stop, and
  // the natively focusable fold buttons / group <summary>s inside it add none (AC 4).
  return { ok: true, tab_stops: sequentialTabStops(dom.trace) };
}

async function runTreeGroups() {
  const dom = keyboardGroupDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();

  // Navigate to the group wrapper: Down (seq 1 -> phase 2), Down (phase 2 -> group).
  fireKey(dom.list, "ArrowDown");
  fireKey(dom.list, "ArrowDown");
  const groupBefore = { expanded: dom.group.getAttribute("aria-expanded"), open: dom.groupDetails.open };

  fireKey(dom.list, "ArrowRight");  // open the group
  const groupAfterOpen = { expanded: dom.group.getAttribute("aria-expanded"), open: dom.groupDetails.open };

  fireKey(dom.list, "ArrowRight");  // move to the first child
  fireKey(dom.list, "Enter"); await settle();  // select the revealed child
  const selChild = selectedNodeSeq();

  // Left from the child returns to the group row; Left again closes it.
  fireKey(dom.list, "ArrowLeft");
  fireKey(dom.list, "ArrowLeft");
  const groupAfterClose = { expanded: dom.group.getAttribute("aria-expanded"), open: dom.groupDetails.open };

  // The repetition wrapper opens the same way and its child is then selectable.
  fireKey(dom.list, "End");         // to the last visible row (the repeat wrapper)
  fireKey(dom.list, "ArrowRight");  // open it
  const repeatExpanded = dom.repeat.getAttribute("aria-expanded");
  fireKey(dom.list, "ArrowRight");  // to the first child
  fireKey(dom.list, "Enter"); await settle();
  const selRepeatChild = selectedNodeSeq();

  return { ok: true, groupBefore, groupAfterOpen, sel_child: selChild,
    groupAfterClose, repeat_expanded: repeatExpanded, sel_repeat_child: selRepeatChild,
    tab_stops: sequentialTabStops(dom.trace), synth_clicks: synthClicks.length };
}

function keyboardActionDom() {
  // A run-level tab group (Trace active, Artifacts inactive) whose Trace panel holds a
  // `.trace-list` with existing ROW ACTIONS: a raw-jump link (jump to the Raw tab
  // pre-filtered to a seq range) on an aggregate node, and a recovery-artifact link
  // (open the escalation report) on another. The Artifacts panel holds the
  // escalation.md entry the recovery link reveals. These actions must stay
  // keyboard-OPERABLE while the tree column keeps exactly one sequential tab stop.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const rawJump = el("a", { classes: ["raw-jump"], attrs: { href: "?raw_from_seq=5&raw_to_seq=9" } });
  const n5 = el("li", { classes: ["node"],
    attrs: { "data-seq": "5", "data-node-type": "phase", "style": "--depth:1" },
    children: [el("span", { classes: ["label"] }), rawJump] });
  const recovery = el("a", { classes: ["recovery-artifact"],
    attrs: { "data-recovery-artifact": "escalation.md", href: "#" } });
  const recoveryCard = el("section", { classes: ["recovery-card"],
    attrs: { "data-recovery-card": "", "data-recovery-kind": "none" }, children: [recovery] });
  const n6 = el("li", { classes: ["node"],
    attrs: { "data-seq": "6", "data-node-type": "agent.tool.call", "style": "--depth:1" },
    children: [el("span", { classes: ["label"] }), recoveryCard] });
  const list = el("ul", { classes: ["trace-list"], children: [n5, n6] });
  const trace = el("div", { classes: ["trace"], children: [list] });
  const panes = el("div", { classes: ["panes"], children: [treePane(5), treePane(6)] });

  const btnTrace = el("button", { classes: ["tab-btn", "active"], attrs: { type: "button", "data-tab": "trace" } });
  const btnArt = el("button", { classes: ["tab-btn"], attrs: { type: "button", "data-tab": "artifacts" } });
  const tabButtons = el("div", { classes: ["tab-buttons"], attrs: { role: "tablist" },
    children: [btnTrace, btnArt] });
  const tracePanel = el("section", { classes: ["tab", "tab-trace", "active"],
    attrs: { "data-tab-panel": "trace" }, children: [trace, panes] });
  const artSummary = el("summary", { classes: ["artifact-open"], attrs: { "data-artifact": "escalation.md" } });
  const artMore = el("a", { attrs: { "data-artifact-more": "", href: "#" } }); artMore.hidden = true;
  const artDetails = el("details", { classes: ["artifact-wrap"],
    children: [artSummary, el("pre", { attrs: { "data-artifact-body": "" } }), artMore] });
  const artPanel = el("section", { classes: ["tab", "tab-artifacts"],
    attrs: { "data-tab-panel": "artifacts" }, children: [artDetails] });
  const tabs = el("div", { classes: ["tabs", "run-tabs"], attrs: { "data-tabs": "" },
    children: [tabButtons, tracePanel, artPanel] });
  const main = el("main", { classes: ["detail"], children: [tabs] });
  body.append(main);
  return { body, trace, list, rawJump, recovery, artPanel, tracePanel, artDetails };
}

async function runTreeActions() {
  const dom = keyboardActionDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();

  // AC 4 / contract: even with action links present, the tree column is ONE tab stop
  // — the links are managed (roving tabindex="-1"), not extra sequential stops.
  const tab_stops = sequentialTabStops(dom.trace);
  const links_managed = dom.rawJump.getAttribute("tabindex") === "-1"
    && dom.recovery.getAttribute("tabindex") === "-1";

  // Keyboard activation of the raw-jump link performs its raw-range navigation.
  fireKey(dom.rawJump, "Enter"); await settle();
  const raw_navs = navigations.slice();

  // Keyboard activation of the recovery link opens the Artifacts tab and reveals the
  // escalation entry — its existing behaviour, not swallowed by node selection.
  fireKey(dom.recovery, "Enter"); await settle();
  const recovery = {
    artifacts_active: dom.artPanel.classes.has("active"),
    trace_active: dom.tracePanel.classes.has("active"),
    escalation_open: dom.artDetails.open,
  };

  return { ok: true, tab_stops, links_managed, raw_navs, recovery };
}

function timelineRowDom() {
  // A dummy in-tree node/pane (initial selection, no fetch), an IN-window timeline
  // row whose node has a pane (seq 10) and an OUT-of-window row whose node has
  // neither pane nor tree row (seq 99). Each row is `.tl-bar-row` > label + track >
  // bar, all carrying data-seq, as the server renders it.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  const tree = el("div", { classes: ["trace"],
    children: [el("li", { classes: ["node"], attrs: { "data-seq": "1" } }),
      el("li", { classes: ["node"], attrs: { "data-seq": "10" } })] });
  function row(seq) {
    const label = el("span", { classes: ["tl-bar-label"], attrs: { "data-seq": String(seq) } });
    const bar = el("span", { classes: ["tl-bar"], attrs: { "data-seq": String(seq) } });
    const track = el("span", { classes: ["tl-track"], children: [bar] });
    const r = el("div", { classes: ["tl-bar-row"], attrs: { "data-seq": String(seq) },
      children: [label, track] });
    return { row: r, label, bar };
  }
  const rin = row(10);
  const rout = row(99);
  const laneBody = el("div", { classes: ["tl-lane-body"], children: [rin.row, rout.row] });
  const lane = el("div", { classes: ["tl-lane"], children: [laneBody] });
  const lanes = el("div", { classes: ["timeline-lanes"], children: [lane] });
  const panes = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "1" } }),
      el("div", { classes: ["pane"], attrs: { "data-seq": "10" } })] });
  body.append(tree, lanes, panes);
  return { body, rowIn: rin.row, labelIn: rin.label, barIn: rin.bar, rowOut: rout.row,
    pane10: panes.children[1] };
}

async function runTimelineActivate() {
  const dom = timelineRowDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();

  // A click on the LABEL (not the 6px bar) selects the in-window node in place.
  dispatch("click", { target: dom.labelIn }); await settle();
  const afterLabel = { pane10_selected: dom.pane10.classes.has("selected"), navs: navigations.slice() };

  // Enter on the OUT-of-window row navigates via ?focus (its node has no pane/row).
  fireKey(dom.rowOut, "Enter"); await settle();
  const afterKeyOut = { navs: navigations.slice() };

  // Space on the in-window row triggers the same in-place selection and must not
  // scroll the page.
  const space = fireKey(dom.rowIn, " "); await settle();
  const afterKeySpace = { pane10_selected: dom.pane10.classes.has("selected"),
    space_default_prevented: space.defaultPrevented };

  // A click on the bar INSIDE the row selects once — no double activation.
  const before = countMeasure("adw:select");
  dispatch("click", { target: dom.barIn }); await settle();
  const barClickMeasures = countMeasure("adw:select") - before;

  return { ok: true, row_tabindex: dom.rowIn.getAttribute("tabindex"),
    afterLabel, afterKeyOut, afterKeySpace, bar_click_measures: barClickMeasures };
}

function tabPatternDom() {
  // The run-level tab group with the RAW tab pre-selected server-side (as a
  // raw_from_seq landing does): `active` on the raw button and its panel. The client
  // must adopt THAT selection, not reset to the first tab. Inside the (hidden) trace
  // panel sits a nested agent.run tab group (prompt/answer/tools) so a test can prove
  // arrow-key switching stays inside its nearest group.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  function btn(name, active) {
    return el("button", { classes: active ? ["tab-btn", "active"] : ["tab-btn"],
      attrs: { "type": "button", "data-tab": name } });
  }
  function panel(name, active, kids) {
    return el("section", { classes: active ? ["tab", "active"] : ["tab"],
      attrs: { "data-tab-panel": name }, children: kids || [] });
  }
  // nested (agent.run) group, prompt active.
  const innerButtons = el("div", { classes: ["tab-buttons"], attrs: { "role": "tablist" },
    children: [btn("prompt", true), btn("answer", false), btn("tools", false)] });
  const innerGroup = el("div", { classes: ["tabs"], attrs: { "data-tabs": "" },
    children: [innerButtons, panel("prompt", true), panel("answer", false), panel("tools", false)] });
  const traceInner = el("div", { classes: ["trace"],
    children: [el("li", { classes: ["node"], attrs: { "data-seq": "1" } })] });
  const panesInner = el("div", { classes: ["panes"],
    children: [el("div", { classes: ["pane"], attrs: { "data-seq": "1" } })] });
  const tracePanel = panel("trace", false, [traceInner, panesInner, innerGroup]);
  const outerButtons = el("div", { classes: ["tab-buttons"], attrs: { "role": "tablist" },
    children: [btn("trace", false), btn("timeline", false), btn("artifacts", false), btn("raw", true)] });
  const outer = el("div", { classes: ["tabs", "run-tabs"], attrs: { "data-tabs": "" },
    children: [outerButtons, tracePanel, panel("timeline", false), panel("artifacts", false),
      panel("raw", true)] });
  body.append(outer);
  return { body, outer, outerButtons, innerGroup, innerButtons };
}

function outerButton(dom, name) {
  let found = null;
  dom.outerButtons.querySelectorAll(".tab-btn").forEach((b) => {
    if (!found && b.getAttribute("data-tab") === name) found = b;
  });
  return found;
}
function innerButton(dom, name) {
  let found = null;
  dom.innerButtons.querySelectorAll(".tab-btn").forEach((b) => {
    if (!found && b.getAttribute("data-tab") === name) found = b;
  });
  return found;
}

async function runTabPattern() {
  const dom = tabPatternDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();

  // --- AC 8: roles, single selected tab (the server-preselected RAW), aria-controls
  // pointing at a role="tabpanel".
  const roles = ["trace", "timeline", "artifacts", "raw"].map((n) => outerButton(dom, n).getAttribute("role"));
  const rawBtn = outerButton(dom, "raw");
  const controls = rawBtn.getAttribute("aria-controls");
  let controlsPanelName = null;
  let controlsPanelRole = null;
  dom.outer.querySelectorAll("[data-tab-panel]").forEach((p) => {
    if (controls && p.closest("[data-tabs]") === dom.outer && p.getAttribute("id") === controls) {
      controlsPanelName = p.getAttribute("data-tab-panel");
      controlsPanelRole = p.getAttribute("role");
    }
  });
  const initial = tabGroupState(dom.outer);
  const outerInit = { roles, selected: initial.selected, selected_count: initial.selectedCount,
    tab_stops: initial.tabStops, controls_panel: controlsPanelName, controls_role: controlsPanelRole };

  // --- AC 9: ArrowLeft moves the active tab (raw -> artifacts); selection class,
  // visible panel, roving tab stop and aria-selected all travel together.
  fireKey(rawBtn, "ArrowLeft"); await settle();
  const afterArrow = { selected: tabGroupState(dom.outer).selected,
    active_button: activeButtonTab(dom.outer), visible_panel: activePanelName(dom.outer),
    tab_stops: tabGroupState(dom.outer).tabStops,
    roving_at: (function () {
      let at = null;
      dom.outerButtons.querySelectorAll(".tab-btn").forEach((b) => {
        if (b.closest("[data-tabs]") === dom.outer && (b.getAttribute("tabindex") === "0") && !at) at = b.getAttribute("data-tab");
      });
      return at;
    })() };

  // A CLICK switch updates aria-selected too (not only the arrow keys).
  dispatch("click", { target: outerButton(dom, "timeline") }); await settle();
  const afterClick = { selected: tabGroupState(dom.outer).selected };

  // --- AC 9 nesting: an arrow inside the inner group switches only the inner group;
  // the outer group's selection is untouched.
  const innerBefore = tabGroupState(dom.innerGroup).selected;
  const outerBefore = tabGroupState(dom.outer).selected;
  fireKey(innerButton(dom, "prompt"), "ArrowRight"); await settle();
  const nested = { inner_before: innerBefore, inner_after: tabGroupState(dom.innerGroup).selected,
    outer_before: outerBefore, outer_after: tabGroupState(dom.outer).selected };

  return { ok: true, outerInit, afterArrow, afterClick, nested };
}

function selectionMarkedDom() {
  // A tree with a dummy first node (seq 1), two selectable nodes (seq 10, seq 20) and
  // a timeline row/bar for seq 10 — enough to exercise selection over the tree AND
  // the timeline, by mouse AND keyboard.
  const body = el("body", { attrs: { "data-repo": "repo", "data-run-id": "aaaa1111" } });
  function node(seq) {
    return el("li", { classes: ["node"], attrs: { "data-seq": String(seq),
      "data-node-type": "agent.tool.call", "style": "--depth:1" },
      children: [el("span", { classes: ["label"] })] });
  }
  const n1 = node(1); const n10 = node(10); const n20 = node(20);
  const list = el("ul", { classes: ["trace-list"], children: [n1, n10, n20] });
  const trace = el("div", { classes: ["trace"], children: [list] });
  const bar10 = el("span", { classes: ["tl-bar"], attrs: { "data-seq": "10" } });
  const row10 = el("div", { classes: ["tl-bar-row"], attrs: { "data-seq": "10" },
    children: [el("span", { classes: ["tl-bar-label"], attrs: { "data-seq": "10" } }),
      el("span", { classes: ["tl-track"], children: [bar10] })] });
  const lanes = el("div", { classes: ["timeline-lanes"], children: [row10] });
  function pane(seq) { return el("div", { classes: ["pane"], attrs: { "data-seq": String(seq) } }); }
  const panes = el("div", { classes: ["panes"], children: [pane(1), pane(10), pane(20)] });
  body.append(trace, lanes, panes);
  return { body, list, n10, n20, bar10 };
}

async function runSelectionMarked() {
  const dom = selectionMarkedDom();
  installGlobals(el("html", { children: [dom.body] }), dom.body);
  loadAppJs(APP);
  await settle();

  const initial = ariaSelectedSeqs();                        // the initial selection is marked

  dispatch("click", { target: dom.n10 }); await settle();
  const afterMouse = ariaSelectedSeqs();                     // mouse selection marks seq 10

  fireKey(dom.list, "ArrowDown");                            // nav cursor seq 10 -> seq 20 (no select)
  const afterNavOnly = ariaSelectedSeqs();                   // still seq 10 — a cursor is not a selection

  fireKey(dom.list, "Enter"); await settle();
  const afterKeyboard = ariaSelectedSeqs();                  // keyboard selection marks seq 20

  dispatch("click", { target: dom.bar10 }); await settle();
  const afterTimeline = ariaSelectedSeqs();                  // timeline selection marks seq 10

  return { ok: true, initial, afterMouse, afterNavOnly, afterKeyboard, afterTimeline };
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
  else if (SCENARIO === "openstate-swap") result = await runOpenStateSwap();
  else if (SCENARIO === "refresh-window") result = await runRefreshWindow(ARG || "");
  else if (SCENARIO === "timeline-focus") result = await runTimelineFocus();
  else if (SCENARIO === "trace-focus-fold") result = await runTraceFocusFold();
  else if (SCENARIO === "tree-keyboard") result = await runTreeKeyboard();
  else if (SCENARIO === "tree-fold-keys") result = await runTreeFoldKeys();
  else if (SCENARIO === "tree-tabstop") result = await runTreeTabStop();
  else if (SCENARIO === "tree-groups") result = await runTreeGroups();
  else if (SCENARIO === "tree-actions") result = await runTreeActions();
  else if (SCENARIO === "timeline-activate") result = await runTimelineActivate();
  else if (SCENARIO === "tab-pattern") result = await runTabPattern();
  else if (SCENARIO === "selection-marked") result = await runSelectionMarked();
  else if (SCENARIO === "context-panel") result = await runContextPanel();
  else if (SCENARIO === "context-live-swap") result = await runContextLiveSwap();
  else if (SCENARIO === "cost-format") result = await runCostFormat(ARG);
  else if (SCENARIO === "pretty-payload") result = await runPrettyPayload(ARG);
  else if (SCENARIO === "lazy-pane") result = await runLazyPane();
  else if (SCENARIO === "lazy-pane-race") result = await runLazyPaneRace();
  else if (SCENARIO === "artifact") result = await runArtifact();
  else if (SCENARIO === "recovery-link") result = await runRecoveryLink();
  else if (SCENARIO === "recovery-live") result = await runRecoveryLive();
  else throw new Error("unknown scenario: " + SCENARIO);
  process.stdout.write(JSON.stringify(result));
})().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
