# UV-K5 V3 (PY32F071) simulator

Runs the **unmodified** firmware under [Renode](https://renode.io) so the display,
keyboard, serial line, RF and storage can be driven from a host — for fast
iteration and AI-driven CI.

## Design rule

The firmware must never know it is running in a simulator. There are **no
`#ifdef SIM` patches** and no simulator-specific build: the simulator runs the
exact `Fusion` binary built from this branch. All hardware fidelity lives in
Renode peripheral models under `sim/renode/`.

## Layout

```
sim/
  platforms/py32f071.repl   Renode platform: Cortex-M0+, RAM/flash, NVIC/SysTick,
                            USART1, and the custom PY32F071 peripheral models.
  scripts/run.resc          Loads models + platform + firmware, opens a UART
                            socket (:3456) and a GDB server (:3333).
  scripts/boottest.resc     Headless smoke test (boots, reports CPU state).
  renode/*.cs               Custom C# peripheral models (compiled at load time).
```

## Fresh clone — prerequisites & quickstart

Install once (not vendored in the repo):

- **Renode** — the emulator ([renode.io](https://renode.io)); must be on `PATH`.
  On Arch/Manjaro: the AUR `renode` package or the portable release.
- **ARM toolchain** — `arm-none-eabi-gcc` 13.x + `cmake` + `ninja` (or use Docker,
  `./compile-with-docker.sh Fusion`). `arm-none-eabi-gdb` for `debug.sh` / the MCP
  `gdb` tool.
- **Python deps** — `pip install -r ../requirements.txt` (pyserial, Pillow;
  websockets / pygame / mcp are optional).

Known-good versions (this project is built and simulated against these):

| Tool | Tested version |
|---|---|
| Renode | v1.16.1 |
| arm-none-eabi-gcc | 16.1.0 (also builds with 13.x) |
| arm-none-eabi-gdb | 17.2 |
| cmake | 4.3.4 |
| ninja | 1.13.2 |
| Python | 3.14 |
| pyserial / Pillow / websockets / mcp | 3.5 / 12.3 / 13.1 / 1.27 |

Then a single command builds the firmware, creates the (gitignored) flash image,
and runs the sim:

```bash
pip install -r ../requirements.txt
./sim/dev.sh --no-viewer      # builds Fusion ELF + flash image, then runs
```

The MCP server (`tools/mcp/`) can do the same steps as tools — `build_firmware`,
`sim_start`, `screen`, etc. — so an assistant can build/run/inspect without shell.

## Build the firmware

`dev.sh` builds it for you; to build by hand:

```bash
cmake --preset Fusion && cmake --build --preset Fusion -j
# -> build/Fusion/f4hwn.fusion.elf  (the binary the simulator runs)
```

## Run

```bash
./sim/dev.sh                                # build + reload a running sim (or start one)
./sim/dev.sh --no-build                     # skip the build
./sim/dev.sh --restart                      # force a cold start
renode sim/scripts/run.resc                 # interactive, hardware-faithful timing
renode --console --plain sim/scripts/boottest.resc   # headless smoke test
```

`dev.sh` is the GUI loop: it rebuilds, gets the new firmware running, waits until the
radio has really booted, and leaves the web viewer serving on http://localhost:8088/.
**~4s when it can reload a running sim, ~14s for a cold start.**

Reload keeps the same Renode process alive: `run.resc`'s reset macro re-runs `LoadELF`,
so `machine Reset` brings the machine back on the freshly built binary. The PTY belongs
to the process rather than the machine, so it survives — the bridge keeps its port and
**the browser stays connected across a reload**; edit, run `dev.sh`, watch the screen
come back. Memory does not clear on reset (`MappedMemory.Reset` is a no-op), so the
flash and EEPROM images are re-seeded by hand, otherwise a reload would inherit
whatever the previous run wrote.

While a viewer is attached, `dev.sh` does not open the serial port itself: two readers
would split the byte stream and steal the frames the viewer needs.

It also runs the core at `PerformanceInMips 10` (override with `MIPS=`). Virtual time
advances as instructions/MIPS, so a *lower* value gets more simulated time out of the
same host work: at Renode's default of 100 the firmware's 2.55 s boot screen costs
~60 s of wall clock and a CHIRP round trip takes minutes; at 10 they cost ~2 s and a
few seconds. The emulated core is then slower relative to its timers than the real
PY32 — fine for UI work (screen, key injection and a 122-channel CHIRP round trip are
all verified at this setting), but use `run.resc` directly when chasing a
timing-sensitive bug.

Note the firmware streams a burst during its boot screen and then goes quiet for the
rest of the boot, so the first frame does not mean it is ready — wait for streaming
that *stays* (`dev.sh` does).

## Peripheral model status

| Peripheral | Bus | Model | State |
|---|---|---|---|
| ADC1 (battery) | mmio 0x40012400 | `PY32_ADC.cs` | done — calibration + conversion |
| USART1 | mmio 0x40013800 | `PY32_UART.cs` | done — TX + circular RX DMA (bidirectional serial) |
| DMA1 | mmio 0x40020000 | Renode `STM32LDMA` | done (channel-enable transfer + TC IRQ) |
| SPI2 + PY25Q16 flash | mmio 0x40003800 + PA3 CS | `PY32_SPIFlash.cs`, file-backed | done — correct polling + DMA reads, program/erase |
| GPIOA / GPIOB / GPIOF | mmio 0x50000000+ | Renode `STM32_GPIOPort` | done (flash CS, BK4819, keyboard) |
| GPIOC | mmio 0x50000800 | stubbed high | not yet needed |
| BK4819 radio | bit-bang GPIO PF9/PB8/PB9 | `PY32_BK4819.cs` | done — boots through RADIO_SetupRegisters |
| keyboard matrix | GPIOB cols/rows + PTT | `PY32_KeyMatrix.cs` | done — holds "no key" across resets (injection via serial) |
| 24Cxx EEPROM / BK1080 | bit-bang I2C PF5/PF6 | `PY32_I2CBus.cs` | done — decoder + 8 KB EEPROM (file) + BK1080 stub |

**Boot status:** the unmodified firmware boots all the way to its **rendered VFO
screen** (dual-VFO main display, battery level, etc.). On V3 the settings +
calibration live in the PY25Q16 SPI flash (`settings.c` → `PY25Q16_ReadBuffer`;
`eeprom_compat.c` maps the logical EEPROM layout onto flash sectors); the I2C bus
only carries the BK1080 (FM). The flash image (`sim/data/spi_PY25Q16.bin`) is
generated by `make_flash_image.py` with synthesized minimal battery calibration so
the firmware leaves `gReducedService` and draws normally; channels/settings stay
at defaults.

Capture the screen by reading `gStatusLine` (128 B) + `gFrameBuffer` (896 B) over
the monitor/GDB and piping into `sim/renode/fb_to_png.py`.

## Scripting the radio, and UI regression tests

`sim/uvctl.py` drives the radio from a script — no browser needed:

```bash
./sim/uvctl.py screenshot --text      # the LCD as block art on stdout
./sim/uvctl.py screenshot -o s.png
./sim/uvctl.py press MENU 1 EXIT      # inject keys (--long for a long press)
./sim/uvctl.py wait-ready
```

It reads the screen out of emulated RAM over the Renode monitor (`gStatusLine` +
`gFrameBuffer`, resolved from the ELF) and only *writes* keys to the serial port. That
split matters: two readers on the port would split the byte stream and steal frames
from a watching browser, but writing is harmless — so this works with the viewer open,
and you can watch scripted keypresses land on screen.

`uvctl` can also read the firmware's own state, which makes scripted UI work deterministic:
`menu_goto()` walks to a named menu entry by reading `MenuList[gMenuCursor]` instead of
counting keypresses (the list wraps, the screen lags a press behind, and entries are hidden
per build), and `read_setting()` reads a `gEeprom` field using the field offset out of the
DWARF, so nothing hand-counts a struct layout.

`sim/test_persist.py` checks that a setting the user changes actually survives a power
cycle — the regression that otherwise only shows up on real hardware, weeks later, when a
radio forgets everything as its battery comes out:

```bash
./sim/test_persist.py            # change it in the menu, cold-restart, read it back
./sim/test_persist.py --only cw
```

Each case changes a setting through the real UI, asserts it reached the flash image on
disk, then kills the simulator, cold-starts it, and asserts the firmware parsed the value
back. Nothing is stubbed: it goes settings.c → eeprom_compat.c → PY25Q16_WriteBuffer →
SPI2 → the image, and back. Verified to catch a real regression by deleting the CW block's
write from `settings.c`: both CW cases failed, naming the flash address, while the
unrelated squelch case still passed.

`sim/test_cw_tx.py` checks the radio actually sends correct Morse:

```bash
./sim/test_cw_tx.py            # types a letter, presses PTT, measures the keying
./sim/test_cw_tx.py --only 15
```

The keying is recorded from the BK4819 as the firmware keys it -- the PA on REG_33 (OOK)
or the TX mute on REG_50 (AFCW) -- with *emulated* timestamps, so the measurement does not
depend on how fast Renode runs (`UVK5_CW_KEYLOG=<file>` switches the recording on). A dot
is 1200/WPM ms, a dash is three dots, the gap between elements is one dot; the test asserts
that shape at several speeds. PTT cannot be injected over serial (the firmware blocks it on
purpose), so the key matrix pulls the real pin via the `ptt_press` / `ptt_release` monitor
commands.

It found a real bug: every element and gap ran one 10ms tick long, so a 15 WPM dot was 90ms
instead of 80 and dash/dot was 2.78 rather than 3 -- worse at speed, since at 25 WPM that
tick is +25%. Note the firmware can only key whole 10ms ticks, so above ~20 WPM the dot is
quantised (at 25 WPM `dit_ticks` is 4, i.e. 40ms rather than the ideal 48ms); the test
checks what the firmware can actually produce and reports the quantisation.

`sim/test_ui.py` uses it for golden-screen tests:

```bash
./sim/test_ui.py            # compare each screen against sim/golden/*.png
./sim/test_ui.py --update   # re-record the goldens (review the diff!)
./sim/test_ui.py --only menu
```

Each case reboots the radio first, so cases cannot inherit each other's state. The
rendered screen is deterministic to the pixel, so a mismatch is a real change: a
failure writes the actual screen and a red-highlighted diff to `sim/golden/failed/`.
Verified by breaking a menu label on purpose — the three menu screens failed and the
other three passed.

## Debugging (GDB)

```bash
./sim/debug.sh                                   # build, load the sim, attach GDB
./sim/debug.sh -ex "break APP_Update" -ex "continue"
```

Breakpoints, stepping, registers and typed variables all work — the release build now
carries `-g3`, which puts DWARF in the `.elf` and leaves the flashed `.bin` byte for
byte identical (verified: `text=103780` with and without it).

Debugging is a *separate entry point* from `dev.sh` for a reason. Renode's GDB server
is started with `autostartEmulation`, so GDB starts the emulation on attach and owns
the CPU — which only works if nobody called `start` first. Attach to an already-running
machine, as `dev.sh` leaves it, and GDB reports "target is running" and refuses to break
or step, because Renode never halts the core for it. `debug.sh` therefore loads the
machine and leaves it stopped for GDB to drive.

The web viewer is served as usual, so you can watch the screen freeze on a breakpoint,
and `uvctl.py press` still reaches the radio while GDB is attached and running — that is
how you trigger a breakpoint that needs a keypress.

## Web viewer (live screen + keyboard)

`sim/webviewer/bridge.py` runs the **K5Viewer** web app against the simulator: the
live 128×64 screen in the browser, and its keypad injects real keypresses into the
firmware. That is the fast GUI loop — edit, build, run, look.

```bash
renode -e "include @sim/scripts/run.resc; start"   # sim -> /tmp/ttyUV0
python3 sim/webviewer/bridge.py                    # -> http://localhost:8088/
```

Then click **Connect** in the page (the serial port picker is bypassed).

The viewer speaks Web Serial, and Chrome only enumerates real tty devices — it can
never see Renode's PTY. So the bridge serves the upstream viewer with a small
`navigator.serial` shim injected (`serial-ws-shim.js`), backed by a WebSocket that
owns the PTY. The viewer's own code is untouched and stays updatable; it cannot tell
it isn't talking to a radio. Because Web Serial is no longer used, this also works in
Firefox. Point `--viewer` at any K5Viewer checkout; `--port` at another PTY.

## CHIRP

CHIRP talks to the simulator exactly as it would to the radio — point it at the
PTY. A full download → upload → reboot → download round trip reproduces the
uploaded channels byte for byte.

```bash
cd ~/code/chirp
R="Quansheng_UV-K1_&_UV-K5_V3_F4HWN_Fusion"
CHIRP_TESTENV=1 python3 ./chirpc -r "$R" -s /tmp/ttyUV0 --mmap=radio.img --download-mmap
CHIRP_TESTENV=1 python3 ./chirpc -r "$R" -s /tmp/ttyUV0 --mmap=radio.img --upload-mmap
```

`CHIRP_TESTENV=1` is needed because CHIRP otherwise redirects stdout to
`~/.config/chirp/debug.log` when stdin is not a TTY. A successful `--upload-mmap`
still exits 1 (upstream `chirp/cli/main.py` quirk) — trust "Upload successful",
not the exit code.

Renode runs slower than real time, so the firmware's 2.55 s boot screen takes
tens of seconds of wall clock — and CHIRP's upload ends by rebooting the radio.
Poll the hello until it answers rather than sleeping a fixed amount; a hello sent
too early fails with "Header short read" and looks like a dead UART.

## Gotcha: models that hold a constant GPIO level

Renode's `GPIO.Set()` is a no-op when the level is unchanged. A GPIO port clears
its input pins on reset while a driving model still caches "high", so the idle
level is silently never re-propagated. `PY32_KeyMatrix` therefore re-drives its
rows and PTT low→high from `IMachine.MachineReset`, which is the only hook that
runs after every peripheral has been reset (`Machine.Reset()` is otherwise
unordered). Without it, the firmware sees PTT held down after a reboot and sits
forever in the "RELEASE ALL KEYS" loop, never servicing serial. Models that
toggle their line during normal traffic (BK4819, I2C SDA) self-heal and don't
need this.

## Where the radio's configuration lives

**All of it is in the SPI flash — `sim/data/spi_PY25Q16.bin` — and it persists.**

`App/driver/eeprom.c` (the I2C 24Cxx driver) is *not compiled* on V3. The build uses
`App/driver/eeprom_compat.c`, which keeps the logical EEPROM addresses the rest of the
firmware knows about and translates them onto the PY25Q16 serial flash:

```
CHIRP write (UART 0x051D)   settings save          menu / channel edit
  App/app/uart.c:468          App/settings.c            ...
        \                         |                      /
         \________________________|_____________________/
                                  v
              EEPROM_ReadBuffer / EEPROM_WriteBuffer
              App/driver/eeprom_compat.c   (ADDR_MAPPINGS: logical EEPROM -> flash)
                                  v
              PY25Q16_ReadBuffer / PY25Q16_WriteBuffer   App/driver/py25q16.c
                                  v
              SPI2 + chip-select bit-banged on GPIOA pin 3
                                  v
              sim/renode/PY32_SPIFlash.cs  (0x03 read, 0x02 program, 0x20 erase)
                                  v
              spiFlashMem @ 0x90000000   ->  mirrored into sim/data/spi_PY25Q16.bin
```

The model mirrors every page-program and sector-erase into the backing file at the same
offset, so whatever the firmware saves — channels, settings, calibration, everything
CHIRP uploads — survives a restart, exactly as it would on the real part. (Renode memory
is otherwise not written through to disk, and `MappedMemory.Reset()` is a no-op, so the
images are also re-seeded on reset.) Verified: upload 122 channels with CHIRP, cold-start
the simulator, read them back unchanged.

The I2C EEPROM (`sim/data/eeprom.bin`) is vestigial on V3 — nothing addresses 0xA0, that
bus only really carries the BK1080 — so it is generated erased.

Override the images with `FLASH_IMAGE=` / `EEPROM_IMAGE=`, which is how `test_ui.py`
gets a factory-fresh radio without touching the one you have configured. Regenerate the
defaults with `python3 sim/make_flash_image.py` (that resets your simulated radio).

`sim/renode/flash_persist.py` still provides `save_flash` / `load_flash` monitor commands
for taking or restoring a snapshot by hand.

Unmodelled on-chip registers (RCC, FLASH, SPI status, etc.) are stubbed with
`sysbus Tag` in `run.resc` and will be replaced by real models as needed.
