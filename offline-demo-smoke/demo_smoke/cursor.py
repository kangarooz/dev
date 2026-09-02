"""Visible mouse cursor for screencast / headless recordings.

Chrome's screencast (and headless mode) never draws the OS pointer, so we
inject a small script on every new document that renders an arrow which
follows ``mousemove`` and pulses on ``mousedown``.  It has
``pointer-events: none`` and the maximum z-index, so it never interferes
with the page under test.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CURSOR_ID = "demo-smoke-cursor"

CURSOR_JS = r"""
(function () {
  if (window.__demoSmokeCursorInstalled) { return; }
  window.__demoSmokeCursorInstalled = true;
  var ID = "demo-smoke-cursor";
  var state = { x: -100, y: -100, downs: 0, el: null };
  window.__demoSmokeCursor = state;

  function build() {
    if (!document.documentElement || document.getElementById(ID)) { return; }
    var style = document.createElement("style");
    style.id = ID + "-style";
    style.textContent =
      "#" + ID + "{position:fixed;left:0;top:0;width:24px;height:24px;margin:0;padding:0;" +
      "pointer-events:none;z-index:2147483647;opacity:0;transform:translate(-2px,-2px);" +
      "filter:drop-shadow(0 1px 1px rgba(0,0,0,.45));}" +
      "#" + ID + " svg{display:block;width:24px;height:24px;}" +
      "#" + ID + "-pulse{position:absolute;left:12px;top:12px;width:36px;height:36px;margin:-18px 0 0 -18px;" +
      "border-radius:50%;background:rgba(43,108,176,.35);border:2px solid rgba(43,108,176,.9);" +
      "transform:scale(0);opacity:0;}" +
      "#" + ID + ".demo-smoke-down #" + ID + "-pulse{animation:demo-smoke-pulse 320ms ease-out;}" +
      "@keyframes demo-smoke-pulse{0%{transform:scale(.2);opacity:.9}100%{transform:scale(1);opacity:0}}";
    var el = document.createElement("div");
    el.id = ID;
    el.setAttribute("aria-hidden", "true");
    el.setAttribute("data-x", "-100");
    el.setAttribute("data-y", "-100");
    el.setAttribute("data-downs", "0");
    el.innerHTML =
      '<div id="' + ID + '-pulse"></div>' +
      '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">' +
      '<path d="M5 3 L5 19 L9.5 15.2 L12.4 21.4 L15.2 20.1 L12.4 14 L18 14 Z" ' +
      'fill="#ffffff" stroke="#111111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(style);
    document.documentElement.appendChild(el);
    state.el = el;
    if (state.x >= 0) { place(state.x, state.y); }
  }

  function place(x, y) {
    state.x = x; state.y = y;
    var el = state.el || document.getElementById(ID);
    if (!el) { return; }
    el.style.left = x + "px";
    el.style.top = y + "px";
    el.style.opacity = "1";
    el.setAttribute("data-x", String(x));
    el.setAttribute("data-y", String(y));
  }

  document.addEventListener("mousemove", function (e) { place(e.clientX, e.clientY); }, true);
  document.addEventListener("mousedown", function (e) {
    place(e.clientX, e.clientY);
    state.downs += 1;
    var el = state.el || document.getElementById(ID);
    if (!el) { return; }
    el.setAttribute("data-downs", String(state.downs));
    el.classList.remove("demo-smoke-down");
    void el.offsetWidth; // restart the animation
    el.classList.add("demo-smoke-down");
    window.setTimeout(function () { el.classList.remove("demo-smoke-down"); }, 350);
  }, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  }
  build();
})();
"""


def install(cdp) -> None:
    """Inject ``CURSOR_JS`` into every new document of the page behind ``cdp``.

    Enables the Page domain on ``cdp`` (required for new-document scripts) and
    also evaluates the script in the current document so the cursor is visible
    before the first navigation.  Never raises for the evaluate part: an
    ``about:blank`` page without a documentElement is fine.
    """
    # New-document scripts only fire on a session that has the Page domain enabled.
    cdp.send("Page.enable")
    cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": CURSOR_JS})
    try:
        cdp.send("Runtime.evaluate", {"expression": CURSOR_JS, "awaitPromise": False})
    except Exception:
        log.debug("cursor evaluate on the current document failed (fine on about:blank)", exc_info=True)
