#!/usr/bin/env python3
"""
Chrome Control - local bridge.

Two modes in one file:

  serve    Listen on 127.0.0.1:8765 for the Chrome Control extension.
           Run this and leave it running; the extension reconnects on its own.

  <cmd>    Send one command to the extension and print the JSON result.

Examples
--------
  uv run --with websockets python bridge.py serve
  uv run --with websockets python bridge.py ping
  uv run --with websockets python bridge.py tabs
  uv run --with websockets python bridge.py nav https://example.com
  uv run --with websockets python bridge.py go --tab 123 "document.title"
  uv run --with websockets python bridge.py text --tab 123
  uv run --with websockets python bridge.py cookies mail.google.com
  uv run --with websockets python bridge.py wait --tab 123 "document.title" --timeout 15000
"""

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    sys.exit("missing dependency: pip install websockets   (or: uv run --with websockets python bridge.py ...)")


HOST = "127.0.0.1"
PORT = 8765
WS_URI = f"ws://{HOST}:{PORT}"
MAX_MSG = 32 * 1024 * 1024


# ------------------------------------------------------------------ server

async def serve(host: str, port: int) -> None:
    async with websockets.serve(_on_client, host, port, max_size=MAX_MSG):
        print(f"chrome-control bridge listening on ws://{host}:{port}", flush=True)
        print("waiting for the Chrome Control extension to connect...", flush=True)
        await asyncio.Future()


async def _on_client(ws) -> None:
    print("extension connected", flush=True)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("event") == "hello":
                print(f"hello from extension v{msg.get('version')}", flush=True)
    except Exception as exc:
        print(f"extension disconnected: {exc}", flush=True)


# ------------------------------------------------------------------ client

async def call(payload: dict, timeout: float) -> dict:
    async with websockets.connect(WS_URI, max_size=MAX_MSG) as ws:
        await ws.send(json.dumps(payload))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("extension did not respond in time")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError("extension did not respond in time")
            msg = json.loads(raw)
            if msg.get("id") == payload["id"]:
                return msg


def build_payload(args) -> dict:
    nid = id(args) % 10_000_000
    cmd = args.cmd

    if cmd == "ping":
        return {"id": nid, "cmd": "ping"}
    if cmd == "tabs":
        return {"id": nid, "cmd": "list_tabs"}
    if cmd == "nav":
        return {"id": nid, "cmd": "navigate", "url": args.arg, "tabId": args.tab}
    if cmd == "go":
        return {"id": nid, "cmd": "eval", "tabId": args.tab, "expression": args.arg, "world": args.world}
    if cmd == "text":
        return {"id": nid, "cmd": "text", "tabId": args.tab}
    if cmd == "activate":
        return {"id": nid, "cmd": "activate", "tabId": args.tab}
    if cmd == "close":
        return {"id": nid, "cmd": "close", "tabId": args.tab}
    if cmd == "cookies":
        return {"id": nid, "cmd": "cookies", "domain": args.arg}
    if cmd == "wait":
        return {"id": nid, "cmd": "wait_for", "tabId": args.tab,
                "expression": args.arg, "timeoutMs": args.timeout}
    raise SystemExit(f"unknown command: {cmd}")


async def run_client(args) -> int:
    try:
        resp = await call(build_payload(args), timeout=args.timeout / 1000.0 + 15)
    except (OSError, TimeoutError) as exc:
        print(f"cannot reach the bridge at {WS_URI}: {exc}", file=sys.stderr)
        print("is `bridge.py serve` running, and is the extension installed?", file=sys.stderr)
        return 2

    if resp.get("ok"):
        print(json.dumps(resp.get("result"), indent=2, ensure_ascii=False))
        return 0
    print("ERROR:", resp.get("error"), file=sys.stderr)
    return 1


# ------------------------------------------------------------------ main

def main() -> int:
    parser = argparse.ArgumentParser(description="Chrome Control bridge")
    sub = parser.add_subparsers(dest="mode")

    s = sub.add_parser("serve", help="run the bridge server")
    s.add_argument("--host", default=HOST)
    s.add_argument("--port", type=int, default=PORT)

    for name in ("ping", "tabs", "nav", "go", "text", "activate", "close", "cookies", "wait"):
        p = sub.add_parser(name)
        p.add_argument("arg", nargs="?", default=None, help="URL, expression, or cookie domain")
        p.add_argument("--tab", type=int, default=None)
        p.add_argument("--world", default="isolated", choices=["isolated", "main"])
        p.add_argument("--timeout", type=int, default=60000, help="ms")

    argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 2

    args = parser.parse_args(argv)
    if args.mode == "serve":
        try:
            asyncio.run(serve(args.host, args.port))
        except KeyboardInterrupt:
            pass
        return 0
    return asyncio.run(run_client(args))


if __name__ == "__main__":
    sys.exit(main())
