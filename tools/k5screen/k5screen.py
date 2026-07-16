#!/usr/bin/env python3
"""k5screen — one live-screen viewer for the real radio and the simulator.

The F4HWN firmware streams its 128x64 LCD over serial as a delta-compressed
frame protocol (the "K5Viewer" protocol). The simulator runs the same firmware
and emits the identical stream on its emulated UART (/tmp/ttyUV0), so a single
decoder drives both — only the port differs. This unifies the old pygame-only
`tools/k5viewer/k5viewer.py` (real radio) and `sim/uvctl.py`'s ASCII rendering
(sim, via Renode RAM) into one tool that speaks the wire protocol for either.

    # Auto-detect (prefers the sim PTY if present, else a USB serial radio):
    ./k5screen.py                     # live ASCII in the terminal, keys forwarded

    ./k5screen.py --sim               # force the simulator (/tmp/ttyUV0)
    ./k5screen.py --port /dev/ttyACM0 # force a specific port
    ./k5screen.py --gui               # pygame window (needs pygame)

    ./k5screen.py --once              # print one settled frame and exit (scriptable)
    ./k5screen.py --png screen.png    # save one settled frame as PNG (needs Pillow)
    ./k5screen.py --keys "MENU 1 EXIT"  # inject keys, then view (append ! for long press)

Protocol (from App/screenshot.c, host side in tools/k5viewer):
    keepalive host->radio : 55 AA 00 00   (must be sent to keep the stream alive)
    key inject host->radio: AA 55 03 <k>  short,  AA 55 04 <k>  long
    frame radio->host     : [F0|flags] AA 55 <type> <len_be16> <payload>
        type 0x01 = full 1024-byte frame; 0x02 = diff, len%9==0, each record
        = <block 0..127><8 bytes>. The framebuffer is ROW-MAJOR bit-packed
        (bit index = y*128 + x), not the ST7565 page-column order.
"""

import argparse
import os
import sys
import time

import serial
from serial.tools import list_ports

WIDTH, HEIGHT = 128, 64
FRAME_SIZE = WIDTH * HEIGHT // 8          # 1024
BAUDRATE = 38400
SIM_PTY = os.environ.get("UVK5_SIM_PTY", "/tmp/ttyUV0")

HEADER = b"\xAA\x55"
TYPE_FULL = 0x01
TYPE_DIFF = 0x02
KEEPALIVE = b"\x55\xAA\x00\x00"

# host -> radio key codes (same map as sim/uvctl.py so scripts agree)
KEYS = {
    "0": 0x00, "1": 0x01, "2": 0x02, "3": 0x03, "4": 0x04,
    "5": 0x05, "6": 0x06, "7": 0x07, "8": 0x08, "9": 0x09,
    "MENU": 0x0A, "UP": 0x0B, "DOWN": 0x0C, "EXIT": 0x0D,
    "STAR": 0x0E, "F": 0x0F, "PTT": 0x10, "SIDE2": 0x11, "SIDE1": 0x12,
}
ALIASES = {"*": "STAR", "#": "F", "M": "MENU", "F1": "SIDE1", "F2": "SIDE2"}

# state flags carried in the 0xF0|flags marker byte (real radio)
FLAG_DEEP_SLEEP = 1 << 0
FLAG_LED_RED = 1 << 1
FLAG_LED_GREEN = 1 << 2


# --------------------------------------------------------------------------- #
# Serial stream decoder                                                        #
# --------------------------------------------------------------------------- #

class Stream:
    """Decodes the delta frame protocol off a serial port into a row-major fb."""

    def __init__(self, ser):
        self.ser = ser
        self.fb = bytearray(FRAME_SIZE)
        self.flags = 0
        self._last = 0  # last byte read, to catch the 0xF0|flags marker

    def keepalive(self):
        try:
            self.ser.write(KEEPALIVE)
            self.ser.flush()
        except serial.SerialException:
            pass

    def send_key(self, name, long_press=False):
        key = ALIASES.get(name.upper(), name.upper())
        if key not in KEYS:
            raise ValueError(f"unknown key '{name}' — one of: {', '.join(KEYS)}")
        self.ser.write(bytes([0xAA, 0x55, 0x04 if long_press else 0x03, KEYS[key]]))
        self.ser.flush()

    def read_frame(self):
        """Read one frame, updating self.fb/self.flags. None on timeout."""
        ser = self.ser
        while True:
            b = ser.read(1)
            if not b:
                return None
            byte = b[0]
            if byte == HEADER[0] and ser.read(1) == HEADER[1:2]:
                # a 0xF0..0xF7 byte immediately before the header carries LED/sleep flags
                if 0xF0 <= self._last <= 0xF7:
                    self.flags = self._last & 0x07
                t = ser.read(1)
                size = int.from_bytes(ser.read(2), "big")
                if t == bytes([TYPE_FULL]) and size == FRAME_SIZE:
                    self.fb = bytearray(ser.read(FRAME_SIZE))
                    self._last = 0
                    return self.fb
                if t == bytes([TYPE_DIFF]) and size % 9 == 0:
                    self._apply_diff(ser.read(size))
                    self._last = 0
                    return self.fb
                self._last = 0
            else:
                self._last = byte

    def _apply_diff(self, payload):
        i = 0
        while i + 9 <= len(payload):
            block = payload[i]
            if block >= 128:
                break
            self.fb[block * 8: block * 8 + 8] = payload[i + 1: i + 9]
            i += 9


def capture_settled(stream, seconds=1.2):
    """Pump keepalives and read frames for a moment, returning the latest fb.

    A fresh keepalive makes the firmware force a full frame, so a short window
    reliably yields a complete, current screen."""
    deadline = time.monotonic() + seconds
    last_ka = 0.0
    got = False
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now - last_ka >= 0.3:
            stream.keepalive()
            last_ka = now
        if stream.read_frame() is not None:
            got = True
    if not got:
        sys.exit("no frames received — is the radio on the K5Viewer screen / firmware "
                 "streaming? (the port may be held by Chirp/Chrome, or the radio is off)")
    return stream.fb


# --------------------------------------------------------------------------- #
# Renderers (row-major framebuffer)                                            #
# --------------------------------------------------------------------------- #

def bit(fb, x, y):
    idx = y * WIDTH + x
    return (fb[idx >> 3] >> (idx & 7)) & 1


def as_text(fb):
    """Two pixel rows per line as half-block glyphs (32 lines x 128 cols)."""
    glyphs = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    out = []
    for y in range(0, HEIGHT, 2):
        out.append("".join(glyphs[(bit(fb, x, y), bit(fb, x, y + 1))]
                           for x in range(WIDTH)))
    return "\n".join(out)


def save_png(fb, path, scale=4):
    try:
        from PIL import Image
    except ImportError:
        sys.exit("--png needs Pillow:  pip install pillow")
    img = Image.new("1", (WIDTH, HEIGHT))
    px = img.load()
    for x in range(WIDTH):
        for y in range(HEIGHT):
            px[x, y] = bit(fb, x, y)
    img.resize((WIDTH * scale, HEIGHT * scale), Image.NEAREST).save(path)


# --------------------------------------------------------------------------- #
# Source selection                                                             #
# --------------------------------------------------------------------------- #

def usb_serial_ports():
    return [p.device for p in list_ports.comports() if p.vid is not None]


def resolve_port(args):
    if args.port:
        return args.port
    if args.sim:
        if not os.path.exists(SIM_PTY):
            sys.exit(f"--sim: {SIM_PTY} not found — is ./sim/dev.sh running?")
        return SIM_PTY
    # auto: prefer the sim PTY, else the first USB serial device
    if os.path.exists(SIM_PTY):
        return SIM_PTY
    ports = usb_serial_ports()
    if not ports:
        sys.exit("no port found — plug in the radio, start the sim, or pass --port")
    return ports[0]


# --------------------------------------------------------------------------- #
# Live views                                                                   #
# --------------------------------------------------------------------------- #

# terminal key -> (radio key, long?)
CURSES_KEYMAP = {
    ord("m"): ("MENU", False), ord("\n"): ("MENU", False), ord("M"): ("MENU", True),
    ord("e"): ("EXIT", False), 127: ("EXIT", False), ord("E"): ("EXIT", True),
    ord("*"): ("STAR", False), ord("#"): ("F", False),
    ord("f"): ("F", False), ord("F"): ("F", True),
    ord("o"): ("SIDE1", False), ord("k"): ("SIDE2", False),
    ord("O"): ("SIDE1", True), ord("K"): ("SIDE2", True),
    ord(" "): ("PTT", False),
}
LONG_ARM = ord("\t")   # Tab: make the *next* key a long press (works for digits/arrows too)
for _d in "0123456789":
    CURSES_KEYMAP[ord(_d)] = (_d, False)


def run_curses(stream):
    import curses

    def loop(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        scr.timeout(0)
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        last_ka = 0.0
        pending_long = False   # Tab arms a one-shot long press for the next key
        legend = ("keys: 0-9  m=MENU  e/⌫=EXIT  ↑↓=UP/DOWN  *  f=F(#) o/k=side  "
                  "Tab or CAPS=long  space=PTT   q=quit")
        while True:
            now = time.monotonic()
            if now - last_ka >= 0.3:
                stream.keepalive()
                last_ka = now

            ch = scr.getch()
            if ch != -1:
                if ch in (ord("q"), 3):        # q or Ctrl-C
                    return
                if ch == LONG_ARM:
                    pending_long = True
                elif ch == curses.KEY_UP:
                    stream.send_key("UP", pending_long); pending_long = False
                elif ch == curses.KEY_DOWN:
                    stream.send_key("DOWN", pending_long); pending_long = False
                elif ch in CURSES_KEYMAP:
                    name, lng = CURSES_KEYMAP[ch]
                    stream.send_key(name, lng or pending_long); pending_long = False

            if stream.read_frame() is not None:
                rows, cols = scr.getmaxyx()
                text = as_text(stream.fb).split("\n")
                scr.erase()
                for i, line in enumerate(text):
                    if i >= rows - 1:
                        break
                    scr.addnstr(i, 0, line, max(0, cols - 1))
                flag_txt = " ".join(f for f, on in (
                    ("SLEEP", stream.flags & FLAG_DEEP_SLEEP),
                    ("RED", stream.flags & FLAG_LED_RED),
                    ("GRN", stream.flags & FLAG_LED_GREEN)) if on) or "-"
                arm = " LONG-ARMED" if pending_long else ""
                status = f"[{flag_txt}{arm}]  {legend}"
                if rows > len(text):
                    scr.addnstr(rows - 1, 0, status, max(0, cols - 1))
                scr.refresh()
            else:
                time.sleep(0.01)

    curses.wrapper(loop)


def run_gui(stream):
    try:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "hide")
        import pygame
    except ImportError:
        sys.exit("--gui needs pygame:  pip install pygame")

    scale = 6  # initial pixel size; the window is resizable and scales to fit
    fg, bg = pygame.Color(0, 0, 0), pygame.Color(202, 202, 202)
    pygame.init()
    win = pygame.display.set_mode((WIDTH * scale, HEIGHT * scale), pygame.RESIZABLE)
    pygame.display.set_caption("k5screen")
    lcd = pygame.Surface((WIDTH, HEIGHT))   # 1:1 framebuffer; scaled to the window

    # pygame key -> radio key
    kmap = {pygame.K_m: "MENU", pygame.K_RETURN: "MENU", pygame.K_e: "EXIT",
            pygame.K_BACKSPACE: "EXIT", pygame.K_UP: "UP", pygame.K_DOWN: "DOWN",
            pygame.K_ASTERISK: "STAR", pygame.K_HASH: "F", pygame.K_f: "F",
            pygame.K_SPACE: "PTT",
            pygame.K_o: "SIDE1", pygame.K_k: "SIDE2"}
    for d in range(10):
        kmap[getattr(pygame, f"K_{d}")] = str(d)

    def blit(fb):
        # Render the LCD 1:1, then scale it to fit the current window preserving
        # the 2:1 aspect and centre it, so no row/column is ever clipped.
        # Read the live surface each frame (SDL2 resizes it in place); never call
        # set_mode again -- doing so in a resize handler causes a shrink spiral on
        # HiDPI displays where the event size is in points, not pixels.
        surf = pygame.display.get_surface()
        lcd.fill(bg)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                if bit(fb, x, y):
                    lcd.set_at((x, y), fg)
        ww, wh = surf.get_size()
        k = max(1, min(ww // WIDTH, wh // HEIGHT))
        w, h = WIDTH * k, HEIGHT * k
        surf.fill((0, 0, 0))
        surf.blit(pygame.transform.scale(lcd, (w, h)), ((ww - w) // 2, (wh - h) // 2))
        pygame.display.flip()

    last_ka = 0.0
    clock = pygame.time.Clock()
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); return
            if ev.type == pygame.VIDEORESIZE:
                blit(stream.fb)   # just redraw; SDL2 already resized the surface
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_q:
                    pygame.quit(); return
                if ev.key in kmap:
                    stream.send_key(kmap[ev.key], bool(ev.mod & pygame.KMOD_SHIFT))
        now = time.monotonic()
        if now - last_ka >= 0.3:
            stream.keepalive(); last_ka = now
        if stream.read_frame() is not None:
            blit(stream.fb)
        clock.tick(60)


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Live UV-K5 screen viewer (real radio or sim).")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--port", help="serial port (e.g. /dev/ttyACM0)")
    src.add_argument("--sim", action="store_true", help=f"use the simulator ({SIM_PTY})")
    ap.add_argument("--baud", type=int, default=BAUDRATE, help="baud (default 38400; USB CDC ignores it)")
    ap.add_argument("--list-ports", action="store_true", help="list USB serial ports and exit")
    ap.add_argument("--keys", help="inject keys before viewing, e.g. \"MENU 1 EXIT\" (append ! for long)")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="print one settled frame and exit")
    mode.add_argument("--png", metavar="FILE", help="save one settled frame as PNG and exit")
    mode.add_argument("--gui", action="store_true", help="pygame window (needs pygame)")
    ap.add_argument("--scale", type=int, default=4, help="PNG scale factor (default 4)")
    args = ap.parse_args()

    if args.list_ports:
        for p in list_ports.comports():
            if p.vid is not None:
                desc = " - ".join(filter(None, (p.product, p.manufacturer)))
                print(f"- {p.device}" + (f" : {desc}" if desc else ""))
        return

    port = resolve_port(args)
    try:
        ser = serial.Serial(port, args.baud, timeout=0.2)
    except serial.SerialException as e:
        sys.exit(f"cannot open {port}: {e}")
    print(f"k5screen: {port}", file=sys.stderr)
    stream = Stream(ser)

    try:
        if args.keys:
            for tok in args.keys.split():
                long_press = tok.endswith("!")
                stream.send_key(tok.rstrip("!"), long_press)
                time.sleep(0.35)

        if args.png:
            save_png(capture_settled(stream), args.png, args.scale)
            print(f"wrote {args.png}", file=sys.stderr)
        elif args.once:
            print(as_text(capture_settled(stream)))
        elif args.gui:
            run_gui(stream)
        else:
            run_curses(stream)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    main()
