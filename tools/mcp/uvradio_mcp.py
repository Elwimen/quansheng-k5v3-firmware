#!/usr/bin/env python3
"""uvradio — an MCP server for the Quansheng UV-K5 V3 firmware project.

Exposes the real radio and the Renode simulator as MCP tools so an assistant can
flash, drive, view (as images), inspect and debug the radio without shelling out:

  sim lifecycle : sim_start / sim_stop / sim_status
  screen        : screen(target)                -> PNG image + ASCII
  input         : press_keys(target, keys)      transmit(target, seconds)
  flash (radio) : flash_firmware / calib_dump / calib_restore /
                  logo_upload / logo_download / set_ponmsg
  inspect (sim) : read_symbol / read_field / read_mem
  debug (sim)   : gdb(commands)   (needs sim_start mode="debug")

Wraps the existing tools: sim/uvctl.py (Renode monitor), tools/k5screen (serial
screen/keys/PTT), tools/uvflash (stock flasher), arm-none-eabi-gdb (:3333).

Run standalone (stdio):  python3 tools/mcp/uvradio_mcp.py
Register with Claude Code: see tools/mcp/README.md.
"""
import io
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FW = Path(__file__).resolve().parents[2]            # .../firmware
SIM = FW / "sim"
K5 = FW / "tools" / "k5screen"
UVFLASH = FW / "tools" / "uvflash" / "uvflash.py"
ELF = FW / "build" / "Fusion" / "f4hwn.fusion.elf"
SIM_PTY = os.environ.get("UVK5_SIM_PTY", "/tmp/ttyUV0")
GDB = os.environ.get("GDB", "arm-none-eabi-gdb")
DEFAULT_RADIO_PORT = os.environ.get("UVK5_RADIO_PORT", "/dev/ttyACM0")

sys.path.insert(0, str(SIM))
sys.path.insert(0, str(K5))

from mcp.server.fastmcp import FastMCP, Image   # noqa: E402
import serial                                    # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_log import SessionLog               # noqa: E402

mcp = FastMCP("uvradio")

LOG_DIR = Path(os.environ.get("UVRADIO_LOG_DIR", FW / "logs"))
LOG = SessionLog(LOG_DIR, FW, ELF)

# Track how the sim was last launched (run = streaming; debug = GDB-owned/halted).
_sim_mode = None


# --------------------------------------------------------------------------- #
# session logging: wrap the tool manager so EVERY call/result is recorded       #
# --------------------------------------------------------------------------- #

# Tool -> category, so the viewer can colour/filter/scan without a lookup table.
CATEGORY = {
    "build_firmware": "build", "clean_build": "build",
    "sim_start": "sim", "sim_stop": "sim", "sim_status": "sim",
    "screen": "screen",
    "press_keys": "input", "goto_menu": "input", "transmit": "input",
    "flash_firmware": "flash", "calib_dump": "flash", "calib_restore": "flash",
    "logo_upload": "flash", "logo_download": "flash", "set_ponmsg": "flash",
    "read_symbol": "debug", "read_field": "debug", "read_mem": "debug", "gdb": "debug",
    "open_log_viewer": "log", "log_info": "log", "log_note": "log",
}


def _install_logging():
    """Log every tool call at the one choke point the MCP handler routes through.

    FastMCP's request handler calls `self._tool_manager.call_tool(...)` by attribute
    lookup each time, so patching the manager catches every invocation — no need to
    decorate 19 tools individually.
    """
    tm = mcp._tool_manager
    original = tm.call_tool

    async def logged(name, arguments, **kw):
        cid = LOG.next_id()
        cat = CATEGORY.get(name, "other")
        LOG.event({"type": "call", "id": cid, "tool": name, "cat": cat, "args": arguments})
        t0 = time.monotonic()
        try:
            result = await original(name, arguments, **kw)
        except BaseException as e:
            # BaseException, not Exception: a stray sys.exit() inside a tool (uvctl
            # is a CLI and does that) raises SystemExit and would otherwise kill this
            # server outright. Log it, then re-raise as a normal error.
            LOG.event({"type": "error", "id": cid, "tool": name, "cat": cat, "ok": False,
                       "dur_ms": round((time.monotonic() - t0) * 1000),
                       "error": f"{type(e).__name__}: {e}"})
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                raise RuntimeError(f"{name} tried to terminate the server: {e}") from None
            raise
        texts, images = LOG.harvest(result, cid, name)
        LOG.event({"type": "result", "id": cid, "tool": name, "cat": cat, "ok": True,
                   "dur_ms": round((time.monotonic() - t0) * 1000),
                   "text": "\n".join(texts)[:4000], "images": images})
        return result

    tm.call_tool = logged


_install_logging()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _sh(cmd, timeout=180, cwd=FW):
    """Run a command, capture combined output (never touches MCP stdout)."""
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or ""), p.returncode


def _uvctl(fn, *a, **kw):
    """Call a uvctl helper safely.

    uvctl.py is a CLI first: several helpers (field_offset, _gdb, menu_goto) call
    sys.exit() on failure. SystemExit is a BaseException, so it sails past
    `except Exception` and would TAKE THIS SERVER DOWN. Convert it to a normal
    error the MCP layer can report.
    """
    try:
        return fn(*a, **kw)
    except SystemExit as e:
        raise RuntimeError(f"uvctl: {e}") from None


def _renode_running():
    out, _ = _sh(["pgrep", "-f", "renode.*run.resc"], timeout=10)
    return bool(out.strip())


def _png_and_text_sim(mon=None):
    """Capture the sim LCD from Renode RAM (uvctl). Returns (png_bytes, ascii).

    Pass an existing Monitor to reuse it — Renode's telnet monitor serves one
    connection, so opening a second while one is held just blocks.
    """
    import uvctl
    own = mon is None
    if own:
        mon = uvctl.Monitor()
    try:
        screen = uvctl.grab(mon)
    finally:
        if own:
            mon.close()
    tmp = tempfile.mktemp(suffix=".png")
    uvctl.as_png(screen, tmp, scale=4)
    data = Path(tmp).read_bytes()
    os.unlink(tmp)
    return data, uvctl.as_text(screen)


def _png_and_text_radio(port):
    """Capture the real radio LCD over serial (k5screen). Returns (png_bytes, ascii)."""
    import k5screen
    ser = serial.Serial(port, 38400, timeout=0.2)
    try:
        fb = k5screen.capture_settled(k5screen.Stream(ser))
    finally:
        ser.close()
    tmp = tempfile.mktemp(suffix=".png")
    k5screen.save_png(fb, tmp, scale=4)
    data = Path(tmp).read_bytes()
    os.unlink(tmp)
    return data, k5screen.as_text(fb)


# --------------------------------------------------------------------------- #
# session log / live viewer
# --------------------------------------------------------------------------- #

@mcp.tool()
def open_log_viewer(port: int = 8090, browser: bool = True, restart: bool = False) -> str:
    """Start the live session-log viewer and open it in the default browser.

    Streams this session's JSONL log to a browser timeline — every tool call, its
    result, and the radio screen captured at each step. Idempotent: if the viewer
    is already up on `port` it just (re)opens the page. browser=False to skip the
    launch and only return the URL. restart=True kills a running viewer first —
    use it after editing logviewer.py, since the old process keeps the old UI.
    """
    import socket
    import webbrowser

    url = f"http://127.0.0.1:{port}/"

    def up():
        with socket.socket() as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0

    if restart:
        # [l] so the pattern can't match this pkill's own command line
        _sh(["pkill", "-f", f"[l]ogviewer.py --port {port}"], timeout=10)
        for _ in range(20):
            if not up():
                break
            time.sleep(0.2)

    started = False
    if not up():
        # Detach it: the viewer must outlive this MCP server process.
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent / "logviewer.py"),
             "--port", str(port), "--log-dir", str(LOG_DIR)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, cwd=str(FW))
        for _ in range(40):
            if up():
                started = True
                break
            time.sleep(0.25)
        else:
            return f"viewer did not come up on {port} — try: python3 tools/mcp/logviewer.py"

    opened = False
    if browser:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False

    return (f"log viewer {'started' if started else 'already running'} at {url}\n"
            f"browser {'opened' if opened else 'not opened — open the URL yourself'}\n"
            f"session log: {LOG.path}")


@mcp.tool()
def log_note(text: str) -> str:
    """Drop a labelled divider into the session log / viewer timeline.

    Use it to mark what you're about to do ("testing CW menu", "flashing v2") so a
    long session reads as chapters instead of a flat stream of calls.
    """
    LOG.event({"type": "note", "cat": "log", "text": text})
    return f"noted: {text}"


@mcp.tool()
def log_info() -> str:
    """Where this session's log and artifacts live, and how big it is so far."""
    try:
        n = sum(1 for _ in open(LOG.path, encoding="utf-8"))
    except Exception:
        n = 0
    arts = len(list(LOG.art.glob("*.png")))
    return (f"session : {LOG.session}\nlog     : {LOG.path} ({n} events)\n"
            f"artifacts: {LOG.art} ({arts} images)\nviewer  : open_log_viewer()")


# --------------------------------------------------------------------------- #
# build the firmware
# --------------------------------------------------------------------------- #

@mcp.tool()
def build_firmware(preset: str = "Fusion", options: str = "") -> str:
    """Build the firmware with CMake (configures the preset first if needed).

    preset  : a CMakePresets preset (default "Fusion").
    options : extra CMake -D flags, e.g. "-DENABLE_FEAT_F4HWN_GAME=ON" (forces a
              reconfigure). Returns the result plus the flash/RAM usage summary.
    """
    build_dir = FW / "build" / preset
    if options or not build_dir.exists():
        cfg = ["cmake", "--preset", preset] + (options.split() if options else [])
        out, rc = _sh(cfg, timeout=300)
        if rc != 0:
            return "configure FAILED:\n" + out[-2000:]
    out, rc = _sh(["cmake", "--build", "--preset", preset, "-j"], timeout=600)
    summary = "\n".join(l for l in out.splitlines()
                        if any(k in l for k in ("Memory region", "RAM:", "FLASH:",
                                                "error:", "Error", "FAILED")))
    return f"rc={rc}\n{summary or out[-1500:]}"


@mcp.tool()
def clean_build(preset: str = "Fusion", files: "list[str] | None" = None,
                full: bool = False) -> str:
    """Clean build artifacts — fully or a targeted set of object files.

    full=True : `ninja clean` — remove all compiled outputs (keeps the CMake
                configuration; the next build just recompiles everything).
    files     : repo-relative source paths, e.g. ["App/app/cw.c"], whose object
                files are deleted so ONLY those recompile on the next build —
                handy to force a rebuild of specific TUs without editing them.
    Pass either or both. (Ninja already rebuilds edited files automatically, so a
    targeted clean is only needed to force a recompile.)
    """
    build_dir = FW / "build" / preset
    if not build_dir.exists():
        return f"no build dir: {build_dir} (run build_firmware first)"
    if not full and not files:
        return "nothing to do — pass full=True and/or files=[...]"
    msgs = []
    if full:
        out, rc = _sh(["cmake", "--build", "--preset", preset, "--target", "clean"], timeout=120)
        msgs.append(f"full clean: rc={rc}")
    for src in (files or []):
        # match the full source path (App/app/cw.c vs App/ui/cw.c share a basename)
        hits = list(build_dir.glob(f"CMakeFiles/*.dir/{src}.obj"))
        if not hits:
            msgs.append(f"  {src}: no object found")
        for o in hits:
            o.unlink()
            msgs.append(f"  removed {o.relative_to(FW)}")
    return "\n".join(msgs)


# --------------------------------------------------------------------------- #
# simulator lifecycle
# --------------------------------------------------------------------------- #

@mcp.tool()
def sim_start(mode: str = "run", rebuild: bool = False) -> str:
    """Start (or restart) the Renode simulator.

    mode="run"   : normal — the sim boots and streams its screen/serial (use this
                   for screen/keys/flash-in-sim).
    mode="debug" : loads the machine GDB-ready and does NOT start the CPU, so the
                   gdb() tool can set breakpoints and drive execution.
    rebuild=True : rebuild the Fusion firmware first.
    """
    global _sim_mode
    if rebuild:
        out, rc = _sh(["cmake", "--build", "--preset", "Fusion", "-j"], timeout=600)
        if rc != 0:
            return "build FAILED:\n" + out[-2000:]
    script = "debug.sh" if mode == "debug" else "dev.sh"
    args = [str(SIM / script)]
    if mode == "debug":
        args += ["--no-build"]           # debug.sh execs gdb — but we only want the load
        # debug.sh execs gdb at the end; run it non-interactively so it loads then exits.
        out, rc = _sh(["bash", "-c",
                       f"printf 'detach\\nquit\\n' | {SIM/'debug.sh'} --no-build"], timeout=300)
    else:
        out, rc = _sh(args + ["--restart", "--no-viewer"], timeout=300)
    _sim_mode = mode if rc == 0 else None
    return f"mode={mode} rc={rc}\n" + out[-1500:]


@mcp.tool()
def sim_stop() -> str:
    """Stop the Renode simulator."""
    global _sim_mode
    out, _ = _sh(["pkill", "-f", "renode.*run.resc"], timeout=15)
    _sim_mode = None
    return "stopped" + (("\n" + out) if out.strip() else "")


@mcp.tool()
def sim_status() -> str:
    """Report whether the sim is running, its last-launched mode, and the PTY."""
    return (f"renode_running={_renode_running()}  mode={_sim_mode}  "
            f"pty_exists={os.path.exists(SIM_PTY)}  elf={'ok' if ELF.exists() else 'MISSING'}")


# --------------------------------------------------------------------------- #
# screen (returns an actual image)
# --------------------------------------------------------------------------- #

@mcp.tool()
def screen(target: str = "sim", port: str = DEFAULT_RADIO_PORT) -> list:
    """Capture the 128x64 LCD as a PNG image.

    target="sim"   : the simulator (read from Renode RAM).
    target="radio" : the real radio over USB serial (`port`, default /dev/ttyACM0).
    """
    png, _ = (_png_and_text_radio(port) if target == "radio" else _png_and_text_sim())
    return [Image(data=png, format="png")]


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #

@mcp.tool()
def press_keys(keys: str, target: str = "sim", long: bool = False,
               delay: float = 0.35, human: bool = False, capture: bool = False,
               port: str = DEFAULT_RADIO_PORT) -> list:
    """Inject a sequence of key presses. `keys` is space-separated, e.g. "MENU 1 EXIT".

    Valid: 0-9, MENU/M, UP, DOWN, EXIT, STAR/*, F/#, SIDE1/F1, SIDE2/F2, PTT.
    long=True sends long presses. target "sim" or "radio".

    delay   : seconds between presses (default 0.35).
    human   : when True, randomise each gap (Gaussian ~ delay, with the occasional
              longer pause) to imitate a real operator instead of machine-even timing.
    capture : when True, screenshot immediately after the last key and return the
              image too. Use this for menus — the firmware auto-exits a menu after
              20s of no keypress, so a separate screen() call can race the timeout.
    """
    names = keys.split()

    def gap():
        if not human:
            return delay
        g = random.gauss(delay, delay * 0.4)          # human jitter
        if random.random() < 0.12:                    # occasional "thinking" pause
            g += random.uniform(0.3, 0.9)
        return min(3.0, max(0.06, g))

    if target == "radio":
        import k5screen
        ser = serial.Serial(port, 38400, timeout=0.2)
        try:
            st = k5screen.Stream(ser)
            for i, n in enumerate(names):
                st.send_key(n, long)
                if i < len(names) - 1:
                    time.sleep(gap())
        finally:
            ser.close()
    else:
        import uvctl
        for i, n in enumerate(names):
            uvctl.press([n], long_press=long, delay=0)   # send one; we time the gaps
            if i < len(names) - 1:
                time.sleep(gap())

    msg = f"pressed {names} (long={long}, human={human}, delay~{delay}s) on {target}"
    if not capture:
        return [msg]
    png, _ = (_png_and_text_radio(port) if target == "radio" else _png_and_text_sim())
    return [Image(data=png, format="png"), msg]


@mcp.tool()
def goto_menu(name: str, steps: int = 90) -> list:
    """Open the radio menu and land on a named entry — deterministically (sim only).

    Counting UP/DOWN presses is fragile: the cursor persists between openings, the
    list wraps, and entries are hidden per build. This instead reads the firmware's
    own gMenuCursor/MenuList each step, so it lands correctly whatever the prior
    state. `name` is the on-screen entry, e.g. "CWSpd", "SetGUI", "SQL".
    Returns the resulting screen, so it can't race the 20s menu timeout.
    """
    import uvctl
    mon = uvctl.Monitor()
    try:
        # Reach a known state (EXIT cancels edit mode / leaves the menu), then open it.
        uvctl.press(["EXIT", "EXIT"], delay=0.25)
        time.sleep(0.3)
        uvctl.press(["MENU"], delay=0.3)
        time.sleep(0.4)

        # Entry names are mixed-case in MenuList ("Sql", "CWSpd", "SetLck"), so match
        # case-insensitively rather than make the caller guess the exact casing.
        want = name.strip().lower()
        cur = uvctl.menu_item(mon)
        found = False
        seen = []
        for _ in range(steps):          # ~78 entries, so allow a full lap
            if cur.lower() == want:
                found = True
                break
            seen.append(cur)
            uvctl.press(["UP"], delay=0.2)   # UP wraps; the CW block sits near the end
            time.sleep(0.15)
            cur = uvctl.menu_item(mon)

        png, _ = _png_and_text_sim(mon)         # reuse our monitor, don't nest
        if found:
            status = f"on menu entry '{cur}'"
        else:
            # Help the caller: list what we actually walked past.
            names = ", ".join(dict.fromkeys(seen)) or "(none)"
            status = (f"could NOT reach '{name}' in {steps} steps (now on '{cur}').\n"
                      f"entries seen: {names}")
        return [Image(data=png, format="png"), status]
    finally:
        mon.close()


@mcp.tool()
def transmit(seconds: float = 1.0, target: str = "radio",
             port: str = DEFAULT_RADIO_PORT) -> str:
    """Key the transmitter for a bounded time, then release. RADIATES RF.

    Uses the serial hold-PTT path with keepalives; the firmware's dead-man
    watchdog also auto-releases if anything stalls. Only transmit into a dummy
    load or on a frequency you're authorised to use. seconds is clamped to 10.
    """
    seconds = max(0.1, min(10.0, seconds))
    if target != "radio":
        return "transmit only supported on the real radio"
    import k5screen
    ser = serial.Serial(port, 38400, timeout=0.05)
    try:
        st = k5screen.Stream(ser)
        st.send_ptt(True)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            st.send_ptt(True)
            st.keepalive()
            st.read_frame()
            time.sleep(0.05)
        st.send_ptt(False)
        st.send_ptt(False)
    finally:
        ser.close()
    return f"transmitted ~{seconds:.1f}s, released"


# --------------------------------------------------------------------------- #
# flash / configure the real radio (wraps tools/uvflash)
# --------------------------------------------------------------------------- #

def _uvflash(*args, timeout=120):
    out, rc = _sh(["python3", str(UVFLASH), *args], timeout=timeout)
    return f"rc={rc}\n{out[-1500:]}"


@mcp.tool()
def flash_firmware(bin_path: str = "") -> str:
    """Flash firmware to the radio (must be in BOOTLOADER: hold PTT while powering on).
    Defaults to build/Fusion/f4hwn.fusion.bin."""
    b = bin_path or str(FW / "build" / "Fusion" / "f4hwn.fusion.bin")
    if not Path(b).exists():
        return f"no such file: {b}"
    return _uvflash("flash", b, timeout=180)


@mcp.tool()
def calib_dump(path: str) -> str:
    """Back up the radio's calibration to `path` (radio running normally)."""
    return _uvflash("dump-calib", path)


@mcp.tool()
def calib_restore(path: str) -> str:
    """Restore calibration from `path` to the radio (radio running normally)."""
    return _uvflash("restore-calib", path)


@mcp.tool()
def logo_upload(png_path: str) -> str:
    """Upload a boot logo PNG (128x64) to the radio."""
    return _uvflash("logo-upload", png_path)


@mcp.tool()
def logo_download(png_path: str) -> str:
    """Download the radio's current boot logo to `png_path`."""
    return _uvflash("logo-download", png_path)


@mcp.tool()
def set_ponmsg(mode: str) -> str:
    """Set the power-on message mode (e.g. LOGO / MESSAGE / VOLTAGE / NONE)."""
    return _uvflash("set-ponmsg", mode)


# --------------------------------------------------------------------------- #
# inspect sim memory / symbols (Renode monitor)
# --------------------------------------------------------------------------- #

@mcp.tool()
def read_symbol(name: str, size: int = 4) -> str:
    """Read `size` bytes of a global symbol from the sim and show hex + little-endian int."""
    import uvctl
    mon = uvctl.Monitor()
    try:
        addr = uvctl.symbols()[name]
        raw = mon.read_bytes(addr, size)
    finally:
        mon.close()
    return f"{name} @0x{addr:08X} = {raw.hex(' ')}  (u{size*8}le={int.from_bytes(raw,'little')})"


@mcp.tool()
def read_field(struct: str, field: str, size: int = 4) -> str:
    """Read a field of a global struct instance in the sim by DWARF offset.

    `struct` is the *variable* name (e.g. "gEeprom"); `field` its member. Uses the
    same DWARF offset lookup as sim/uvctl.py."""
    import uvctl
    # Resolve the offset BEFORE opening the monitor: the DWARF lookup shells out to
    # gdb and can fail, and we don't want to hold Renode's single monitor slot while
    # it does. _uvctl() stops uvctl's sys.exit() from killing the server.
    off = _uvctl(uvctl.field_offset, _typeof(struct), field)
    mon = uvctl.Monitor()
    try:
        base = _uvctl(lambda: uvctl.symbols()[struct])
        raw = mon.read_bytes(base + off, size)
    finally:
        mon.close()
    return f"{struct}.{field} (+{off}) = {raw.hex(' ')}  (u{size*8}le={int.from_bytes(raw,'little')})"


def _typeof(var):
    # Best-effort: the two structs uvctl commonly reads.
    return {"gEeprom": "EEPROM_Config_t"}.get(var, var)


@mcp.tool()
def read_mem(addr: str, length: int = 16) -> str:
    """Read `length` bytes from an absolute sim address (hex like 0x20000000)."""
    import uvctl
    a = int(addr, 0)
    mon = uvctl.Monitor()
    try:
        raw = mon.read_bytes(a, length)
    finally:
        mon.close()
    return f"0x{a:08X}: {raw.hex(' ')}"


# --------------------------------------------------------------------------- #
# debug (batch GDB against Renode :3333)
# --------------------------------------------------------------------------- #

@mcp.tool()
def gdb(commands: list[str], timeout: int = 60) -> str:
    """Run a batch of GDB commands against the sim's GDB stub (:3333).

    Requires the sim started with sim_start(mode="debug") so the CPU is halted and
    GDB owns it. Example: ["break APP_Update", "continue", "info registers",
    "backtrace", "print gCurrentFunction"]. GDB attaches, runs the commands, detaches.
    """
    if _sim_mode != "debug":
        return ("sim is not in debug mode — call sim_start(mode='debug') first "
                "(run mode leaves the CPU running, so GDB can't break).")
    args = [GDB, "-q", str(ELF), "-batch", "-ex", "target remote :3333"]
    for c in commands:
        args += ["-ex", c]
    args += ["-ex", "detach"]
    out, rc = _sh(args, timeout=timeout)
    return f"rc={rc}\n{out[-4000:]}"


if __name__ == "__main__":
    mcp.run()
