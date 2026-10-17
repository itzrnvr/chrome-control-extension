// Content script: DOM operations for the background worker.
//
// MV3 forbids eval/new Function in extension contexts (extension CSP has no
// 'unsafe-eval'), so this deliberately exposes discrete DOM operations rather
// than running caller-supplied strings. These cover what page automation
// actually needs and work under any page CSP.
//
// `op: "main"` is the one escape hatch: it injects a <script> into the page's
// own MAIN world. That is governed by the *page's* CSP, not the extension's,
// so it is best-effort and may be blocked on strict sites.

const TMP_ID = "__chrome_control_result";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.__bridge !== "op") return;

  try {
    sendResponse({ ok: true, value: runOp(msg) });
  } catch (e) {
    sendResponse({ ok: false, error: String((e && e.message) || e) });
  }

  return false; // respond synchronously
});

function pick(selector) {
  if (!selector || selector === "body" || selector === ":root") return document.body;
  const el = document.querySelector(selector);
  if (!el) throw new Error("no element matches: " + selector);
  return el;
}

function describe(el) {
  const r = el.getBoundingClientRect();
  const attrs = {};
  for (const a of el.attributes || []) attrs[a.name] = a.value;
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    class: el.className && typeof el.className === "string" ? el.className : null,
    text: (el.innerText || el.textContent || "").trim().slice(0, 500),
    value: "value" in el ? el.value : null,
    href: el.href || null,
    attrs,
    visible: r.width > 0 && r.height > 0,
    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
  };
}

function runOp(msg) {
  const sel = msg.selector;

  switch (msg.op) {
    case "title":
      return document.title;

    case "url":
      return location.href;

    case "text":
      return (pick(sel).innerText || pick(sel).textContent || "").trim();

    case "html":
      return pick(sel).innerHTML;

    case "attr":
      return pick(sel).getAttribute(msg.name);

    case "query": {
      const els = Array.from(document.querySelectorAll(sel || "*"));
      const limit = msg.limit || 100;
      return els.slice(0, limit).map(describe);
    }

    case "count":
      return document.querySelectorAll(sel).length;

    case "click": {
      const el = pick(sel);
      if (!el) throw new Error("no element matches: " + sel);
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      const cx = r.x + r.width / 2;
      const cy = r.y + r.height / 2;
      const top = document.elementFromPoint(cx, cy);
      if (top && !el.contains(top) && !top.contains(el)) {
        throw new Error("element is covered by " + top.tagName.toLowerCase());
      }
      el.click();
      return { clicked: describe(el) };
    }

    case "set": {
      const el = pick(sel);
      if (!("value" in el)) throw new Error("element has no value");
      el.focus();
      el.value = msg.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { set: describe(el) };
    }

    case "wait": {
      // Synchronous best-effort presence check; the caller loops with the
      // `wait` command so it can observe page updates between attempts.
      try {
        return !!document.querySelector(msg.selector);
      } catch (_) {
        return false;
      }
    }

    case "main": {
      // Runs in the page's own world. Subject to the page's CSP.
      const host = document.createElement("div");
      host.id = TMP_ID;
      host.style.display = "none";
      document.documentElement.appendChild(host);
      const s = document.createElement("script");
      s.textContent =
        "try{" +
        "var __r = (function(){" + msg.expression + "})();" +
        "var d=document.getElementById(" + JSON.stringify(TMP_ID) + ");" +
        "d.textContent = JSON.stringify({ok:true, v: __r === undefined ? null : __r});" +
        "}catch(e){var d=document.getElementById(" + JSON.stringify(TMP_ID) + ");" +
        "d.textContent = JSON.stringify({ok:false, e: String(e && e.message || e)});}";
      try {
        document.documentElement.appendChild(s);
        const raw = host.textContent;
        if (!raw) return { ok: false, error: "blocked by page CSP or no result" };
        return JSON.parse(raw);
      } finally {
        s.remove();
        host.remove();
      }
    }

    default:
      throw new Error("unknown op: " + msg.op);
  }
}
