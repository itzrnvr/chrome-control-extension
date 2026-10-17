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

$B status                                # is the extension attached?
$B ping                                  # round-trip check
$B tabs                                  # list open tabs
$B nav https://mail.google.com           # open a tab (--tab N reuses one)
$B activate --tab 123                    # focus a tab
$B close --tab 123                       # close a tab

$B text --tab 123                        # rendered text of a page
$B text --selector "div[role=main]"      # ...of one element
$B html --selector "#inbox"              # innerHTML
$B title                                 # page title
$B url                                   # page url
$B query "tr" --limit 20 --tab 123       # matching elements, described
$B count "a[href]" --tab 123             # number of matches
$B attr "input" name --tab 123           # an attribute
$B click "button[type=submit]" --tab 123 # click (refuses if covered)
$B set "input[name=q]" "hello" --tab 123 # set a value + fire input/change
$B wait "div[role=main]" --tab 123 --timeout 15000
$B main "document.querySelectorAll('a').length"   # page's own JS world
$B cookies mail.google.com               # read cookies
```

`--tab` is optional; it defaults to the active tab.

### Why there is no `eval`

MV3 forbids `eval` and `new Function` in extension contexts: the extension CSP
has no `'unsafe-eval'`. An `eval`-based bridge returns `null` forever and looks
like a dead connection. Hence the discrete DOM operations, which work under any
page CSP. `main` is the escape hatch for page-JS access - it injects a
`<script>` into the page's own world, so the *page's* CSP governs it and strict
sites may block it.

## Reliability

- The extension reconnects forever with capped exponential backoff, and wakes
  itself via `chrome.alarms` if the service worker is evicted.
- A 20s keepalive keeps the worker alive.
- The bridge waits up to 45s for the extension to attach, so starting the
  bridge first or Chrome first both settle correctly.
- `--autolaunch` opens Chrome for you if nothing attaches.

**The most common cause of "no connection" is that the bridge is not running.**
It is a foreground process; close the terminal and it is gone. Run it detached
or via a scheduled task if you want it to persist.

## Gotchas

- **Tabs opened before the extension was installed or reloaded have no content
  script.** Reload them (Ctrl+R / F5). This is different from
- **`chrome://` pages, the Chrome Web Store and the PDF viewer can never be
  scripted** by any extension.
- **No "Allow access to file URLs" is needed.** The extension never reads local
  files; do that from Python instead.
- **Incognito needs its own toggle**, and incognito tabs only appear in `tabs`
  when the extension is allowed there.
- **`click` refuses covered elements** rather than clicking something on top of
  your target.
- **The bridge is loopback-only** (`127.0.0.1`) and holds no credentials.

## Gotchas

- **Tabs opened before the extension was installed or reloaded have no content
  script.** Reload them (Ctrl+R / F5). This is different from
- **`chrome://` pages, the Chrome Web Store and the PDF viewer can never be
  scripted** by any extension.

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
