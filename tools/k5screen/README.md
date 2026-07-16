# k5screen — unified live-screen viewer (real radio **or** simulator)

One tool for watching the UV-K5 128×64 LCD live, from either a real radio over
USB serial or the Renode simulator. Both run the same F4HWN firmware, which
streams the screen with the delta-compressed "K5Viewer" protocol; the simulator
emits the identical stream on its emulated UART (`/tmp/ttyUV0`), so a single
decoder drives both — only the port changes.

This supersedes the two older, single-source tools:

| Old tool | Source | Output | Replaced by |
|---|---|---|---|
| `tools/k5viewer/k5viewer.py` | real radio (serial) | pygame window | `k5screen --gui` |
| `sim/uvctl.py screenshot --text` | sim (Renode RAM) | ASCII | `k5screen --sim --once` |

`sim/uvctl.py` stays — its RAM-read path backs the golden-screen CI tests
(`sim/test_ui.py`) and reads state the serial stream doesn't carry.

## Requirements

- `pyserial` (always)
- `Pillow` — only for `--png`
- `pygame` — only for `--gui`
- a terminal ≥ 128 columns wide for the live ASCII view

## Usage

```bash
# Auto-detect the source (prefers the sim PTY if present, else a USB radio):
./k5screen.py                      # live ASCII in the terminal, keys forwarded

./k5screen.py --sim                # force the simulator (/tmp/ttyUV0)
./k5screen.py --port /dev/ttyACM0  # force a specific port
./k5screen.py --list-ports         # list USB serial ports

./k5screen.py --gui                # pygame window
./k5screen.py --once               # print one settled frame and exit (scriptable)
./k5screen.py --png screen.png     # save one settled frame as PNG
./k5screen.py --keys "MENU 1 EXIT" # inject keys, then view (append ! for a long press)
```

### Live-view keys (forwarded to the radio)

`0`–`9` · `m` = MENU · `e`/`⌫` = EXIT · `↑`/`↓` = UP/DOWN · `*` ·
`f` (or `#`) = F · `o`/`k` = side 1/2 · `space` = PTT · `q` = quit.

**Short vs long press:**
- ASCII (curses): press `Tab` to arm a one-shot **long** press for the next key
  (works for digits and arrows too); or use the CAPS variant of a letter key
  (`M`, `E`, `F`, `O`, `K`). The status line shows `LONG-ARMED`.
- GUI (pygame): hold **Shift** while pressing any key for a long press. The
  window is resizable — drag any edge and the screen scales to fit (2:1),
  never clipping a row or column.
- Scripted (`--keys`): append `!` to a token, e.g. `--keys "F! 1"` (long F, short 1).

## Connecting a real radio

Use a Baofeng/Kenwood-style USB-to-serial cable and put the radio on any normal
screen (the firmware streams whenever a viewer sends keepalives). Close Chirp /
the browser flasher first — they hold the port. The USB CDC port is typically
`/dev/ttyACM0`; `--baud` is accepted but ignored by USB CDC.

## Protocol (see `App/screenshot.c`)

```
keepalive  host→radio : 55 AA 00 00        (sent ~3×/s to keep the stream alive)
key inject host→radio : AA 55 03 <k>       short press;  AA 55 04 <k>  long
frame      radio→host : [F0|flags] AA 55 <type> <len_be16> <payload>
    type 0x01 = full 1024-byte frame
    type 0x02 = diff, len % 9 == 0, each record = <block 0..127><8 bytes>
```

The 1024-byte framebuffer is **row-major** bit-packed (`bit index = y*128 + x`),
*not* the ST7565 page-column order that `sim/uvctl.py` reads out of RAM — hence
the separate renderer here. The optional `F0|flags` marker carries LED/deep-sleep
state (real radio); the ASCII view shows it in the status line.
