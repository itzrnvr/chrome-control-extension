// Popup: read-only status view.
async function refresh() {
  const s = document.getElementById("s");
  const d = document.getElementById("d");
  const dv = document.getElementById("dv");
  const n = document.getElementById("n");
  try {
    const tabs = await chrome.tabs.query({});
    n.textContent = tabs.length;
    dv.className = "dot on";
  } catch (_) {
    dv.className = "dot off";
  }
  chrome.runtime.sendMessage({ __popup: "status" }, (resp) => {
    if (chrome.runtime.lastError || !resp) {
      s.textContent = "service worker asleep";
      d.className = "dot";
      return;
    }
    s.textContent = resp.connected ? "bridge connected" : "bridge not running";
    d.className = resp.connected ? "dot on" : "dot off";
  });
}

document.getElementById("r").addEventListener("click", () => {
  chrome.runtime.sendMessage({ __popup: "reconnect" }, () => setTimeout(refresh, 400));
});
refresh();
