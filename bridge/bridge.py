#!/usr/bin/env python3
"""
Chrome Control - local bridge.

  serve    Listen on 127.0.0.1:8765. The extension holds one long-lived socket;
           CLI clients connect, send one command, get the reply relayed back.

  <cmd>    Send one command to the extension and print the JSON result.

Commands
--------
  status                             is the extension attached?
  ping                               round-trip check
  tabs                               list open tabs
  nav <url>                          open a tab (--tab N to reuse)
  activate --tab N                   focus a tab
  close --tab N                      close a tab
  text [--selector S]                rendered text (default: whole body)
  html [--selector S]                innerHTML
  title | url                        page title / url
  query <css> [--limit N]            matching elements, described
  count <css>                        number of matches
  attr <css> <name>                  an attribute
  click <css>                        click an element
  set <css> <value>                  set an input's value + fire events
  wait <css> [--timeout MS]          poll until the selector appears
  main <expression>                  run JS in the page's own world (page CSP applies)
  cookies [domain]                   read cookies

Examples
--------
  uv run --with websockets python bridge.py serve --autolaunch
  uv run --with websockets python bridge.py text --selector "div[role=main]"
  uv run --with websockets python bridge.py query "tr" --limit 20
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time

try:
    import websockets
except ImportError:
    sys.exit("missing dependency: pip install websockets   (or: uv run --with websockets python bridge.py ...)")


HOST = "127.0.0.1"
PORT = 8765
WS_URI = f"ws://{HOST}:{PORT}"
MAX_MSG = 32 * 1024 * 1024

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def chrome_path():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chrome")


def chrome_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True, text=True, timeout=10,
        ).stdout.lower()
        return "chrome.exe" in out
    except Exception:
        return False


class Hub:
    """Holds the one live extension socket plus in-flight command futures."""

    def __init__(self) -> None:
        self.extension = None
        self.pending: dict = {}
        self._ready = asyncio.Event()
        self.ever_attached = False

    async def attach(self, ws) -> None:
        if self.extension is not None and self.extension is not ws:
            try:
                await self.extension.close()
            except Exception:
                pass
        self.extension = ws
        self._ready.set()
        self.ever_attached = True
        print("extension attached", flush=True)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue
                fut = self.pending.pop(mid, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except Exception as exc:
            print(f"extension link ended: {exc}", flush=True)
        finally:
            if self.extension is ws:
                self.extension = None
                self._ready.clear()
            for fut in list(self.pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("extension disconnected"))
            self.pending.clear()
            print("extension detached", flush=True)

    async def handle_client(self, ws, first: str) -> None:
        loop = asyncio.get_event_loop()
        raw = first
        while True:
            try:
                msg = json.loads(raw)
            except Exception:
                msg = None

            if isinstance(msg, dict) and msg.get("id") is not None:
                await self._relay(ws, raw, msg, loop)

            try:
                raw = await ws.recv()
            except Exception:
                return

    async def _relay(self, ws, raw: str, msg: dict, loop) -> None:
        # Be patient: the extension retries on backoff, so a bridge that just
        # started may need a moment before it answers.
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=45)
        except asyncio.TimeoutError:
            await ws.send(json.dumps({"id": msg["id"], "ok": False,
                                     "error": "extension not connected after 45s - "
                                              "is Chrome running with the extension installed?"}))
            return

        ext = self.extension
        if ext is None:
            await ws.send(json.dumps({"id": msg["id"], "ok": False,
                                     "error": "extension not connected"}))
            return

        fut = loop.create_future()
        self.pending[msg["id"]] = fut
        try:
            await ext.send(raw)
            reply = await asyncio.wait_for(fut, timeout=90)
        except asyncio.TimeoutError:
            self.pending.pop(msg["id"], None)
            reply = {"id": msg["id"], "ok": False,
                     "error": "extension did not respond in time"}
        except Exception as exc:
            self.pending.pop(msg["id"], None)
            reply = {"id": msg["id"], "ok": False, "error": str(exc)}

        try:
            await ws.send(json.dumps(reply))
        except Exception:
            pass


HUB = Hub()


async def serve(host: str, port: int, autolaunch: bool, wait_s: int) -> None:
    async def handler(ws) -> None:
        # The extension greets first with an event; a CLI client sends a
        # command with an id. That first message tells us which it is.
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=15)
        except Exception:
            return
        try:
            msg = json.loads(first)
        except Exception:
            return

        if isinstance(msg, dict) and msg.get("event") == "hello":
            print(f"hello from extension v{msg.get('version')}", flush=True)
            await HUB.attach(ws)
        else:
            await HUB.handle_client(ws, first)

    async def maybe_launch() -> None:
        """If nothing attaches shortly after start, open Chrome for it."""
        deadline = time.time() + wait_s
        while time.time() < deadline and not HUB.ever_attached:
            await asyncio.sleep(1)
        if HUB.ever_attached:
            return
        path = chrome_path()
        if path and not chrome_running():
            print("no extension attached and Chrome is not running; launching Chrome", flush=True)
            subprocess.Popen([path], close_fds=True)
        else:
            print("Chrome is running but no extension attached yet - give it a few "
                  "seconds to reconnect, or load the extension from chrome://extensions",
                  flush=True)

    # The server must stay open for the whole life of this coroutine, so the
    # blocking await has to live inside the `async with`. Anything after the
    # block runs only once the listener has already been torn down.
    async with websockets.serve(handler, host, port, max_size=MAX_MSG):
        print(f"chrome-control bridge listening on ws://{host}:{port}", flush=True)
        print("waiting for the Chrome Control extension...", flush=True)
        if autolaunch:
            asyncio.create_task(maybe_launch())
        await asyncio.Future()


# ------------------------------------------------------------------ client

async def call(payload: dict, timeout: float) -> dict:
    async with websockets.connect(WS_URI, max_size=MAX_MSG) as ws:
        await ws.send(json.dumps(payload))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("no reply from the bridge")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError("no reply from the bridge")
            msg = json.loads(raw)
            if msg.get("id") == payload["id"]:
                return msg


def op_payload(nid, args, op, **extra):
    p = {"id": nid, "cmd": "op", "op": op, "tabId": args.tab}
    for k, v in extra.items():
        if v is not None:
            p[k] = v
    return p


def build_payload(args) -> dict:
    nid = id(args) % 10_000_000
    cmd = args.cmd
    pos = args.positional  # list of trailing positional args

    simple = {
        "ping": lambda: {"id": nid, "cmd": "ping"},
        "status": lambda: {"id": nid, "cmd": "status"},
        "tabs": lambda: {"id": nid, "cmd": "list_tabs"},
    }
    if cmd in simple:
        return simple[cmd]()

    if cmd == "nav":
        return {"id": nid, "cmd": "navigate", "url": pos[0], "tabId": args.tab}
    if cmd == "activate":
        return {"id": nid, "cmd": "activate", "tabId": args.tab}
    if cmd == "close":
        return {"id": nid, "cmd": "close", "tabId": args.tab}
    if cmd == "cookies":
        return {"id": nid, "cmd": "cookies", "domain": pos[0] if pos else None}
    if cmd == "wait":
        return {"id": nid, "cmd": "wait_for", "tabId": args.tab,
                "selector": pos[0], "timeoutMs": args.timeout}

    op_map = {
        "title": "title",
        "url": "url",
        "text": "text",
        "html": "html",
        "query": "query",
        "count": "count",
        "click": "click",
        "set": "set",
        "main": "main",
    }
    if cmd == "attr":
        return op_payload(nid, args, "attr", selector=pos[0], name=pos[1] if len(pos) > 1 else None)
    if cmd in op_map:
        extra = {
            "selector": pos[0] if pos else None,
            "value": pos[1] if cmd == "set" and len(pos) > 1 else None,
            "expression": pos[0] if cmd == "main" else None,
            "limit": args.limit,
        }
        return op_payload(nid, args, op_map[cmd], **extra)

    raise SystemExit(f"unknown command: {cmd}")


async def run_client(args) -> int:
    try:
        resp = await call(build_payload(args), timeout=args.timeout / 1000.0 + 60)
    except (OSError, TimeoutError) as exc:
        print(f"cannot reach the bridge at {WS_URI}: {exc}", file=sys.stderr)
        print("start it with:  bridge.py serve --autolaunch", file=sys.stderr)
        return 2

    if resp.get("ok"):
        print(json.dumps(resp.get("result"), indent=2, ensure_ascii=False))
        return 0
    print("ERROR:", resp.get("error"), file=sys.stderr)
    return 1


# ------------------------------------------------------------------ main

def main() -> int:
    # Page text routinely contains emoji and non-Latin scripts. A cp1252 console
    # would raise UnicodeEncodeError on print, so force UTF-8 output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Chrome Control bridge")
    sub = parser.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the bridge server")
    s.add_argument("--host", default=HOST)
    s.add_argument("--port", type=int, default=PORT)
    s.add_argument("--autolaunch", action="store_true",
                   help="launch Chrome if no extension attaches within --wait seconds")
    s.add_argument("--wait", type=int, default=20, help="seconds before autolaunch fires")

    names = ("status", "ping", "tabs", "nav", "activate", "close", "cookies",
             "title", "url", "text", "html", "query", "count", "attr",
             "click", "set", "wait", "main")
    for name in names:
        p = sub.add_parser(name)
        p.add_argument("positional", nargs="*", help="command arguments")
        p.add_argument("--tab", type=int, default=None)
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--timeout", type=int, default=60000, help="ms")

    argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 2

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        try:
            asyncio.run(serve(args.host, args.port, args.autolaunch, args.wait))
        except KeyboardInterrupt:
            pass
        return 0
    return asyncio.run(run_client(args))


if __name__ == "__main__":
    sys.exit(main())
