#!/usr/bin/env python3
"""Drive the simulated radio from a script: press keys, capture the screen.

    ./sim/uvctl.py screenshot --text          # the LCD as block art on stdout
    ./sim/uvctl.py screenshot -o screen.png
    ./sim/uvctl.py press MENU 1 EXIT          # inject keys
    ./sim/uvctl.py press F1 --long
    ./sim/uvctl.py wait-ready                 # block until the radio has booted

The screen is read out of the emulated RAM over Renode's monitor rather than from the
serial stream, and keys are *written* to the serial port. That split is deliberate:
two readers on the port would split the byte stream between them, so anything that
reads would steal frames from a watching browser -- but writing is harmless. So this
works with the web viewer open, and you can watch your scripted keypresses land.
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ELF = os.path.join(HERE, "..", "build", "Fusion", "f4hwn.fusion.elf")
PTY = "/tmp/ttyUV0"
MONITOR = ("127.0.0.1", 4567)

WIDTH, HEIGHT = 128, 64

# App/driver/keyboard.h; the same codes the K5Viewer keypad sends.
KEYS = {
    "0": 0x00, "1": 0x01, "2": 0x02, "3": 0x03, "4": 0x04,
    "5": 0x05, "6": 0x06, "7": 0x07, "8": 0x08, "9": 0x09,
    "MENU": 0x0A, "UP": 0x0B, "DOWN": 0x0C, "EXIT": 0x0D,
    "STAR": 0x0E, "F": 0x0F, "PTT": 0x10, "SIDE2": 0x11, "SIDE1": 0x12,
}
ALIASES = {"*": "STAR", "#": "F", "M": "MENU", "F1": "SIDE1", "F2": "SIDE2"}


class Monitor:
    """Renode's telnet monitor. Commands are near-instant; read up to the prompt."""

    def __init__(self, addr=MONITOR):
        try:
            self.sock = socket.create_connection(addr, timeout=3)
        except OSError:
            sys.exit(f"no simulator monitor at {addr[0]}:{addr[1]} — is ./sim/dev.sh running?")
        self.sock.settimeout(15)
        self.command("mach set 0")

    def command(self, cmd):
        # Discard anything still pending first. Renode's connect banner ends in a prompt
        # of its own, so a reply read "up to the next prompt" can be one command behind
        # -- and then every read after it returns the *previous* command's output, which
        # is a race that silently hands back plausible-looking garbage.
        self._drain()
        self.sock.sendall((cmd + "\n").encode())
        out = ""
        while "(uvk5v3)" not in out:
            data = self.sock.recv(65536)
            if not data:
                break
            out += data.decode(errors="replace")
        return out

    def _drain(self):
        self.sock.settimeout(0.05)
        try:
            while self.sock.recv(65536):
                pass
        except OSError:
            pass
        finally:
            self.sock.settimeout(15)

    def read_bytes(self, addr, count):
        out = self.command(f"sysbus ReadBytes 0x{addr:08X} {count}")
        return bytes(int(b, 16) for b in re.findall(r"0x([0-9A-Fa-f]{2})[,\]]", out))

    def close(self):
        self.sock.close()


_symbols = None


def symbols():
    """Every symbol moves with every build, so read them from the ELF each time."""
    global _symbols
    if _symbols is None:
        if not os.path.isfile(ELF):
            sys.exit(f"no firmware at {ELF} — build first")
        nm = subprocess.run(["arm-none-eabi-nm", ELF], capture_output=True, text=True).stdout
        _symbols = {p[2]: int(p[0], 16)
                    for p in (line.split() for line in nm.splitlines()) if len(p) == 3}
    return _symbols


def symbol(name):
    addr = symbols().get(name)
    if addr is None:
        sys.exit(f"symbol '{name}' not found in {ELF}")
    return addr


def field_offset(struct, field):
    """Offset of a field, straight out of the DWARF -- no hand-counted struct layouts."""
    out = subprocess.run(
        ["arm-none-eabi-gdb", "-q", ELF, "-batch",
         "-ex", f"print (int)&(({struct} *)0)->{field}"],
        capture_output=True, text=True).stdout
    match = re.search(r"=\s*(\d+)", out)
    if not match:
        sys.exit(f"could not find {struct}.{field} in the debug info: {out.strip()}")
    return int(match.group(1))


def read_setting(mon, field, size=1):
    """Read a gEeprom field out of the running radio's RAM."""
    raw = mon.read_bytes(symbol("gEeprom") + field_offset("EEPROM_Config_t", field), size)
    return int.from_bytes(raw, "little")


def grab(mon):
    """The LCD as 1024 bytes: status line = page 0, framebuffer = pages 1..7."""
    sym = symbols()
    return mon.read_bytes(sym["gStatusLine"], 128) + mon.read_bytes(sym["gFrameBuffer"], 896)


def pixel(screen, x, y):
    idx = (y // 8) * WIDTH + x
    return (screen[idx] >> (y % 8)) & 1 if idx < len(screen) else 0


def as_text(screen):
    """Two pixel rows per line, as half-block glyphs."""
    glyphs = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    lines = []
    for y in range(0, HEIGHT, 2):
        lines.append("".join(glyphs[(pixel(screen, x, y), pixel(screen, x, y + 1))]
                             for x in range(WIDTH)))
    return "\n".join(lines)


def as_png(screen, path, scale=4):
    from PIL import Image
    img = Image.new("1", (WIDTH, HEIGHT))
    px = img.load()
    for x in range(WIDTH):
        for y in range(HEIGHT):
            px[x, y] = pixel(screen, x, y)
    img.resize((WIDTH * scale, HEIGHT * scale), Image.NEAREST).save(path)


def press(keys, long_press=False, delay=0.4):
    """Key packets are write-only, so this never disturbs a viewer's byte stream."""
    import serial
    if not os.path.exists(PTY):
        sys.exit(f"no {PTY} — is the simulator running?")
    kind = 0x04 if long_press else 0x03
    with serial.Serial(PTY, 38400, timeout=1) as ser:
        for name in keys:
            key = name.upper()
            key = ALIASES.get(key, key)
            if key not in KEYS:
                sys.exit(f"unknown key '{name}' — one of: {', '.join(KEYS)}")
            ser.write(bytes([0xAA, 0x55, kind, KEYS[key]]))
            ser.flush()
            time.sleep(delay)


def menu_item(mon):
    """The menu entry the cursor is on, read from MenuList[] rather than the screen.

    Driving menus by counting keypresses is guesswork: the list wraps, entries are
    hidden depending on the build, and the screen lags a keypress behind. The firmware
    knows exactly where it is, so ask it. t_menu_item is { char name[7]; uint8_t id; }.
    """
    cursor = mon.read_bytes(symbol("gMenuCursor"), 1)[0]
    entry = mon.read_bytes(symbol("MenuList") + cursor * 8, 8)
    return entry[:7].split(b"\x00")[0].decode(errors="replace")


def menu_goto(mon, target, steps=90):
    """Open the menu and walk to a named entry (UP wraps, so the CW block is near)."""
    press(["MENU"], delay=0.4)
    time.sleep(0.5)
    for _ in range(steps):   # the menu has ~77 entries, so allow a full lap
        if menu_item(mon) == target:
            return
        press(["UP"], delay=0.35)
        time.sleep(0.3)
    sys.exit(f"could not reach menu entry '{target}' (stuck on '{menu_item(mon)}')")


def wait_ready(mon, timeout=120):
    """Booted = the firmware has drawn something and the picture has settled."""
    deadline = time.time() + timeout
    previous, stable_since = None, None
    while time.time() < deadline:
        screen = grab(mon)
        drawn = any(screen)
        if drawn and screen == previous:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since > 1.0:
                return True
        else:
            stable_since = None
        previous = screen
        time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    shot = sub.add_parser("screenshot", help="capture the LCD")
    shot.add_argument("-o", "--out", help="write a PNG here")
    shot.add_argument("--text", action="store_true", help="print block art instead")

    key = sub.add_parser("press", help="inject keypresses")
    key.add_argument("keys", nargs="+", help=f"any of: {', '.join(KEYS)}")
    key.add_argument("--long", action="store_true", help="long press")
    key.add_argument("--delay", type=float, default=0.4, help="seconds between keys")

    sub.add_parser("wait-ready", help="block until the radio has booted")

    args = ap.parse_args()

    if args.cmd == "press":
        press(args.keys, args.long, args.delay)
        return

    mon = Monitor()
    try:
        if args.cmd == "wait-ready":
            if not wait_ready(mon):
                sys.exit("radio did not settle")
            print("ready")
        else:
            screen = grab(mon)
            if args.out:
                as_png(screen, args.out)
                print(f"wrote {args.out}")
            if args.text or not args.out:
                print(as_text(screen))
    finally:
        mon.close()


if __name__ == "__main__":
    main()
