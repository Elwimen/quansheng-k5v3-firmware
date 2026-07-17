# uvradio — MCP server for the UV-K5 V3 firmware project

Exposes the real radio and the Renode simulator as MCP tools, so an assistant can
flash, drive, view (as images), inspect and debug the radio without shelling out
to bash. Thin wrapper over the existing tooling (`sim/uvctl.py`, `tools/k5screen`,
`tools/uvflash`, `arm-none-eabi-gdb`).

## Tools

| Tool | What it does |
|---|---|
| `open_log_viewer(port, browser, restart)` | start the live session-log viewer and open the default browser |
| `close_log_viewer(port)` | stop the viewer (logging continues; refuses a port held by another app) |
| `log_info()` | where this session's log + artifacts are, and how big |
| `goto_menu(name)` | open the menu and land on a named entry (e.g. `CWSpd`) — deterministic |
| `build_firmware(preset, options)` | build the firmware with CMake; returns flash/RAM usage |
| `clean_build(preset, files, full)` | clean fully (`full`) or delete specific object files to force their rebuild |
| `sim_start(mode, rebuild)` | start/restart the sim — `run` (streaming) or `debug` (GDB-ready, CPU halted) |
| `sim_stop` / `sim_status` | stop / report state |
| `screen(target, port)` | capture the LCD as a **PNG image + ASCII**; `target` = `sim` or `radio` |
| `press_keys(keys, target, long, delay, human)` | inject a key sequence, e.g. `"MENU 1 EXIT"`; `human=True` randomises the gaps like a real operator |
| `transmit(seconds, target)` | key TX for a bounded time (radio; radiates RF — dummy load / clear freq) |
| `flash_firmware(bin_path)` | flash firmware (radio in bootloader) |
| `calib_dump` / `calib_restore` | back up / restore calibration |
| `logo_upload` / `logo_download` | boot logo |
| `set_ponmsg(mode)` | power-on message mode |
| `read_symbol(name, size)` | read a global symbol from the sim (Renode monitor) |
| `read_field(struct, field, size)` | read a struct field by DWARF offset (e.g. `gEeprom.CW_WPM`) |
| `read_mem(addr, length)` | read absolute sim memory |
| `gdb(commands)` | batch GDB against the sim's stub `:3333` (needs `sim_start(mode="debug")`) |

## Requirements

Host Python needs `mcp`, `pyserial`, `Pillow`. Debugging needs `arm-none-eabi-gdb`;
the sim needs Renode (see `sim/`). The real-radio tools need the USB cable
(`/dev/ttyACM0`, override with `UVK5_RADIO_PORT`).

## Register with Claude Code

A project-scoped `.mcp.json` is committed at the repo root:

```json
{ "mcpServers": { "uvradio": { "command": "python3", "args": ["tools/mcp/uvradio_mcp.py"] } } }
```

Claude Code loads it on the next start (approve it when prompted, or
`claude mcp list` to check). MCP servers are loaded at session start, so a running
session must be restarted before the tools appear. Run standalone to smoke-test:

```bash
python3 tools/mcp/uvradio_mcp.py    # stdio; Ctrl-C to exit
```

## Session logging + live browser view

Every tool call and result is logged to `logs/session-<ts>-<id>.jsonl` (gitignored).
One self-contained JSON object per line — append-only, `tail -f`-able, streamable.

```jsonc
{"ts":"…","type":"session","session":"a1b2c3d4","git":{"branch":"develop","sha":"850fffb","dirty":true},"firmware":{…}}
{"ts":"…","type":"call","id":1,"tool":"press_keys","args":{"keys":"MENU"}}
{"ts":"…","type":"result","id":1,"tool":"press_keys","ok":true,"dur_ms":812,
 "text":"pressed […]","images":[{"path":"artifacts/0001-press_keys.png","sha256":"…","bytes":832}]}
```

Design points:
- **Paired `call`/`result`** (same `id`) — the live view can show a call *in flight*
  and time it; failures get their own `error` record.
- **Images by reference**, never base64-inlined: screenshots go to `logs/artifacts/`
  and are referenced by path + hash, so the viewer can render a **visual timeline of
  the radio screen at every step** without bloating the log.
- Line 1 is a **session header** with git branch/SHA/dirty and the firmware build.

View it live:

```bash
python3 tools/mcp/logviewer.py            # http://127.0.0.1:8090
```
…or just call the **`open_log_viewer()`** tool, which starts it and opens your
default browser (`restart=True` after editing the viewer; it verifies `/health` so
it won't mistake an unrelated app squatting the port for the viewer). It's a
**separate process** (Flask + SSE): it survives MCP restarts and can show old sessions.

The UI:

| | |
|---|---|
| **NOW pane** | latest screen always visible + what's in flight |
| **Filmstrip** | every screen capture; click to jump to that call |
| **Categories** | ⚙ build · ▣ sim · ▦ screen · ⌨ input · ⚡ flash · ✱ debug · ✎ log |
| **Duration heat** | dim / amber >1s / red >5s |
| **Dedupe** | identical screen → "screen unchanged" instead of a repeat image |
| **Notes** | `log_note()` chapter dividers |
| **Follow-tail** | sticks to newest, auto-pauses when you scroll up |
| **Filter** | search box, category chips, errors-only |
| **Session picker** | replay any past session (live "newest" by default) |
| **Diff** | `⧉` — green = pixels that turned on, red = turned off |
| **Lightbox** | click any screen to zoom |
| **Keys** | `j`/`k` move · `e` expand · `/` search · `f` follow · `g`/`G` top/bottom · `Esc` close |

> The MCP server speaks protocol on **stdout**, so logging never writes there —
> file only (errors to stderr).

## Notes

- `run` vs `debug` sim: GDB can only break/step when the CPU is halted, which is
  why `debug` mode loads the machine without starting it. Use `run` for
  screen/keys/flash-in-sim; `debug` for `gdb()`.
- `transmit` uses the firmware's watchdog-guarded serial PTT; it self-releases if
  anything stalls, but it still emits RF — use responsibly.
- `screen(target="radio")` briefly holds the serial port; close other viewers
  (Chirp, a browser flasher, a running k5screen) first.
