// Chrome Control - background service worker.
//
// Design notes:
//  - Permissions are granted once, at install time. Chrome does not ask again,
//    and there is no per-connection approval like CDP's "Allow remote debugging?".
//  - The only outbound endpoint is ws://127.0.0.1:<port> (the local bridge).
//  - Reconnects forever with capped exponential backoff, so a Chrome restart or a
//    suspended service worker self-heals without anyone touching anything.
//  - A periodic keepalive over the open socket stops MV3 from idling us out.
//
// MV3 forbids eval/new Function in extension contexts, so page work is done with
// discrete DOM operations (see content.js) rather than caller-supplied strings.

const WS_URL = "ws://127.0.0.1:8765";
const KEEPALIVE_MS = 20000;
const BACKOFF_MIN = 1000;
const BACKOFF_MAX = 15000;

let ws = null;
let retryMs = BACKOFF_MIN;
let retryTimer = null;
let keepTimer = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  clearTimeout(retryTimer);
  retryTimer = null;

  let sock;
  try {
    sock = new WebSocket(WS_URL);
  } catch (_) {
    scheduleRetry();
    return;
  }
  ws = sock;

  sock.onopen = () => {
    retryMs = BACKOFF_MIN; // a successful connect resets backoff
    send({ event: "hello", version: chrome.runtime.getManifest().version, ts: Date.now() });
    startKeepalive();
  };

  sock.onclose = () => {
    stopKeepalive();
    if (ws === sock) ws = null;
    scheduleRetry();
  };

  sock.onerror = () => {
    try { sock.close(); } catch (_) {}
  };

  sock.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (!msg || typeof msg !== "object" || msg.id == null) return;
    try {
      const result = await dispatch(msg);
      send({ id: msg.id, ok: true, result: result === undefined ? null : result });
    } catch (e) {
      send({ id: msg.id, ok: false, error: String((e && e.message) || e) });
    }
  };
}

function scheduleRetry() {
  if (retryTimer) return;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    connect();
  }, retryMs);
  retryMs = Math.min(retryMs * 2, BACKOFF_MAX);
}

function startKeepalive() {
  stopKeepalive();
  keepTimer = setInterval(() => send({ event: "ping", ts: Date.now() }), KEEPALIVE_MS);
}

function stopKeepalive() {
  if (keepTimer) { clearInterval(keepTimer); keepTimer = null; }
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify(obj)); } catch (_) {}
  }
}

const connected = () => !!(ws && ws.readyState === WebSocket.OPEN);

// ---------------------------------------------------------------- dispatch

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("no active tab");
  return tab.id;
}

async function resolveTab(msg) {
  return msg.tabId != null ? msg.tabId : activeTabId();
}

async function dispatch(msg) {
  switch (String(msg.cmd || "").toLowerCase()) {
    case "ping":
      return { pong: true, ts: Date.now(), connected: connected() };

    case "status":
      return { connected: connected(), wsState: ws ? ws.readyState : null };

    case "list_tabs":
      return (await chrome.tabs.query({})).map((t) => ({
        id: t.id, title: t.title, url: t.url,
        status: t.status, active: t.active, windowId: t.windowId
      }));

    case "navigate": {
      if (msg.tabId != null) {
        await chrome.tabs.update(msg.tabId, { url: msg.url });
        return { tabId: msg.tabId, url: msg.url };
      }
      const tab = await chrome.tabs.create({ url: msg.url, active: msg.active !== false });
      return { tabId: tab.id, url: msg.url };
    }

    case "activate": {
      await chrome.tabs.update(msg.tabId, { active: true });
      const w = (await chrome.tabs.get(msg.tabId)).windowId;
      await chrome.windows.update(w, { focused: true }).catch(() => {});
      return { ok: true };
    }

    case "close":
      await chrome.tabs.remove(msg.tabId);
      return { ok: true };

    // -- DOM operations -------------------------------------------------
    case "op": {
      const tabId = await resolveTab(msg);
      return await op(tabId, msg);
    }

    case "wait_for": {
      const deadline = Date.now() + (msg.timeoutMs || 15000);
      const sel = msg.selector;
      let last = null;
      while (Date.now() < deadline) {
        try {
          last = await op(msg.tabId, { op: "wait", selector: sel });
          if (last) return { matched: true, tabId: await resolveTab(msg) };
        } catch (e) {
          last = String((e && e.message) || e);
        }
        await new Promise((r) => setTimeout(r, msg.intervalMs || 400));
      }
      return { matched: false, last };
    }

    case "cookies": {
      const out = await chrome.cookies.getAll(msg.domain ? { domain: msg.domain } : {});
      return out.map((c) => ({
        name: c.name, domain: c.domain, path: c.path, value: c.value,
        secure: c.secure, httpOnly: c.httpOnly,
        expirationDate: c.expirationDate, session: c.session
      }));
    }

    default:
      throw new Error("unknown command: " + msg.cmd);
  }
}

async function op(tabId, msg) {
  try {
    const res = await chrome.tabs.sendMessage(tabId, {
      __bridge: "op",
      op: msg.op,
      selector: msg.selector,
      name: msg.name,
      value: msg.value,
      limit: msg.limit,
      expression: msg.expression
    });
    if (res && res.ok) return res.value;
    throw new Error((res && res.error) || "content script returned no result");
  } catch (e) {
    const err = String((e && e.message) || e);
    // "Receiving end does not exist" means the content script is not in that
    // tab yet - usually because the tab was loaded before the extension was
    // installed or reloaded. That is fixable; a chrome:// URL is not.
    if (/Receiving end does not exist/i.test(err)) {
      throw new Error(
        "no content script in that tab yet. It was probably loaded before the " +
        "extension was installed or reloaded - reload the tab (Ctrl+R / F5) and retry."
      );
    }
    if (/Cannot access a chrome:\/\/ URL/i.test(err)) {
      throw new Error(
        "chrome:// pages, the Chrome Web Store and the PDF viewer cannot be " +
        "scripted by extensions. Use a normal web page instead."
      );
    }
    throw new Error(err);
  }
}

// ---------------------------------------------------------------- lifecycle

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

// Belt and braces: if the worker is ever evicted, this wakes it to reconnect.
chrome.alarms.create("keepalive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => connect());

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.__popup === "status") {
    sendResponse({ connected: connected() });
    return false;
  }
  if (msg && msg.__popup === "reconnect") {
    connect();
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

connect();
