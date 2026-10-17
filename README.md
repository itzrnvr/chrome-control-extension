# Chrome Control

Drive your **real, already-logged-in** Chrome from the command line, with no
permission prompt on every connection.

## Why this exists

Chrome 136+ blocks `--remote-debugging-port` on your default profile. The
supported workaround — the checkbox at `chrome://inspect/#remote-debugging` —
puts Chrome into *approval mode*, where **every incoming connection** shows an
"Allow remote debugging?" dialog. There is no way to persist that approval;
Chrome closed the feature request as not planned.

A normal extension sidesteps this completely. Permissions are granted once when
you install it. After that there is no dialog, ever — not per connection, not
per restart. This project is that approach.

## Layout

```
extension/      Chrome MV3 extension (load this unpacked)
bridge/         Python bridge: server for the extension, CLI for you
```

The extension does browser work only. **All local file and system access lives
in the Python bridge**, which is what keeps the "Allow access to file URLs"
toggle from ever being relevant.

## Install

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked**, select the `extension/` folder.
4. Grant the permission prompt Chrome shows *this one time*.

Done. It survives restarts.

## Run the bridge

```bash
uv run --with websockets python bridge/bridge.py serve
```

Leave it running. The extension dials `ws://127.0.0.1:8765` and reconnects on
its own with capped exponential backoff, so you can start the bridge before or
after Chrome and it will settle either way.

## Commands

```bash
B="uv run --with websockets python bridge/bridge.py"

$B ping                                  # is the extension connected?
$B tabs                                  # list open tabs
$B nav https://mail.google.com           # open a tab
$B go --tab 123 "document.title"         # evaluate JS in a tab
$B go --tab 123 --world main "someFn()"  # evaluate in the page's own context
$B text --tab 123                        # rendered text of a page
$B activate --tab 123                    # focus a tab
$B close --tab 123                       # close a tab
$B cookies mail.google.com               # read cookies for a domain
$B wait --tab 123 "document.title" --timeout 15000
```

`--tab` is optional on `go`, `text` and `wait`; it defaults to the active tab.

## Gotchas, so you don't lose time to them

- **No "Allow access to file URLs" needed.** The extension never reads local
  files. Do that from Python instead.
- **`chrome://` pages are off-limits.** Content scripts cannot run on
  `chrome://`, the Chrome Web Store, other extensions' pages, or the built-in
  PDF viewer. `eval` there returns a clear error rather than hanging. Use the
  normal web for anything you can.
- **Incognito needs its own toggle.** The extension must be separately allowed
  in Incognito, and incognito windows only appear in `tabs` when it is.
- **A restarted Chrome reconnects by itself.** Give it a second or two; the
  extension retries on its own, including after a service-worker eviction.
- **The bridge must be running.** Without it the extension just retries
  silently. The popup icon shows green when connected.
- **The bridge is loopback-only** (`127.0.0.1`) and holds no credentials.

## Security

- The extension's only outbound endpoint is `ws://127.0.0.1:8765`.
- No tokens, keys or profile data are stored in this repository.
- The bridge serves one local client and logs commands to stdout only.
- Granted permissions: `tabs`, `cookies`, `storage`, `scripting`, `alarms`,
  and `<all_urls>` — needed to read and drive arbitrary pages, which is the
  whole point.

Remove the extension from `chrome://extensions` to revoke everything
immediately.

## Licence

MIT. See `LICENSE`.
