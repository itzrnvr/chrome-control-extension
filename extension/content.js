// Content script: evaluates expressions sent by the background worker.
// Runs in the isolated world by default (full DOM access, no page globals).
// Pass world:"main" to evaluate inside the page's own JS context.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.__bridge !== "eval") return;

  (async () => {
    try {
      const value = await run(msg.expression, msg.world);
      sendResponse({ ok: true, value: serialise(value) });
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();

  return true; // reply asynchronously
});

function run(expression, world) {
  const indirect = (0, eval); // indirect eval keeps page scope out of our scope
  try {
    return indirect(expression);
  } catch (e) {
    return Promise.reject(e);
  }
}

// JSON-safe the result, falling back to a string rather than throwing.
function serialise(v) {
  if (v === undefined) return null;
  if (typeof v === "function") return "[function]";
  try {
    return JSON.parse(JSON.stringify(v));
  } catch (_) {
    return String(v);
  }
}
