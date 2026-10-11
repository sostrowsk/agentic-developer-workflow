// Interactive diagrams for the ADW doc exports — inlined by scripts/build_docs.py.
// Contract (see docs/diagrams/README.md): elements with data-step="N" form the
// step-through sequence, data-info="…" is the explanation shown on hover/focus/click,
// class="flow" marks edges whose dashes move.
(function () {
  "use strict";
  var DELAY_MS = 1800;
  var de = (document.documentElement.lang || "").indexOf("de") === 0;
  var T = de
    ? { play: "▶ Abspielen", pause: "⏸ Pause", prev: "◀", next: "▶", all: "Alles zeigen",
        step: "Schritt", hint: "Element anklicken oder überfahren für Details." }
    : { play: "▶ Play", pause: "⏸ Pause", prev: "◀", next: "▶", all: "Show all",
        step: "Step", hint: "Hover or click an element for details." };

  function button(label, title, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    if (title) b.setAttribute("aria-label", title);
    b.addEventListener("click", onClick);
    return b;
  }

  function setup(box) {
    var stepped = Array.prototype.slice.call(box.querySelectorAll("[data-step]"));
    var informed = Array.prototype.slice.call(box.querySelectorAll("[data-info]"));
    var info = document.createElement("div");
    info.className = "dia-info";
    info.setAttribute("aria-live", "polite");
    info.textContent = T.hint;

    function show(el) {
      informed.forEach(function (e) { e.classList.remove("sel"); });
      if (!el) { info.textContent = T.hint; return; }
      el.classList.add("sel");
      info.textContent = el.getAttribute("data-info");
    }

    informed.forEach(function (el) {
      el.setAttribute("tabindex", "0");
      el.setAttribute("role", "button");
      el.addEventListener("mouseenter", function () { show(el); });
      el.addEventListener("focus", function () { show(el); });
      el.addEventListener("click", function () { show(el); });
    });

    box.parentNode.insertBefore(info, box.nextSibling);
    if (!stepped.length) return;

    var steps = [];
    stepped.forEach(function (el) {
      var n = parseInt(el.getAttribute("data-step"), 10);
      if (steps.indexOf(n) < 0) steps.push(n);
    });
    steps.sort(function (a, b) { return a - b; });

    var pos = -1;  // -1 = everything visible
    var timer = null;
    var bar = document.createElement("div");
    bar.className = "dia-bar";
    var label = document.createElement("span");
    label.className = "dia-pos";

    function render() {
      var current = pos < 0 ? null : steps[pos];
      stepped.forEach(function (el) {
        var n = parseInt(el.getAttribute("data-step"), 10);
        el.classList.toggle("dim", current !== null && n > current);
        el.classList.toggle("cur", current !== null && n === current);
      });
      label.textContent = pos < 0 ? "" : T.step + " " + (pos + 1) + " / " + steps.length;
      if (current === null) { show(null); return; }
      var lead = stepped.filter(function (el) {
        return parseInt(el.getAttribute("data-step"), 10) === current && el.hasAttribute("data-info");
      })[0];
      show(lead || null);
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      playBtn.textContent = T.play;
    }

    function go(delta) {
      stop();
      pos = Math.max(0, Math.min(steps.length - 1, pos + delta));
      render();
    }

    var playBtn = button(T.play, null, function () {
      if (timer) { stop(); return; }
      if (pos >= steps.length - 1 || pos < 0) pos = -1;
      playBtn.textContent = T.pause;
      pos += 1;
      render();
      timer = setInterval(function () {
        if (pos >= steps.length - 1) { stop(); return; }
        pos += 1;
        render();
      }, DELAY_MS);
    });

    bar.appendChild(playBtn);
    bar.appendChild(button(T.prev, T.step + " −1", function () { go(-1); }));
    bar.appendChild(button(T.next, T.step + " +1", function () { go(1); }));
    bar.appendChild(button(T.all, null, function () { stop(); pos = -1; render(); }));
    bar.appendChild(label);
    box.parentNode.insertBefore(bar, info);
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".dia"), setup);
  });
})();
