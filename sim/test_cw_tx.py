#!/usr/bin/env python3
"""Does the radio actually send correct Morse?

    ./sim/test_cw_tx.py
    ./sim/test_cw_tx.py --only 15

Types a letter into the CW chat screen, presses PTT, and measures what comes out of the
transmitter -- the real keying, recorded from the BK4819 as the firmware keys it (the PA
on REG_33 in OOK, the TX mute on REG_50 in AFCW). Timestamps are the emulated clock, so
they do not depend on how fast Renode happens to be running.

Morse timing is defined by one number: a dot is 1200/WPM milliseconds. A dash is three
dots, the gap between elements is one dot. Those ratios are what makes it readable, so
that is what this asserts. It is the sort of bug you cannot see on a screen and would
otherwise only hear on the air -- or have someone tell you your fist is unreadable.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.environ.get("TMPDIR", "/tmp"), "uvk5-sim", "cwtx")
FLASH_IMAGE = os.path.join(SANDBOX, "spi_PY25Q16.bin")
KEY_LOG = os.path.join(SANDBOX, "keying.csv")

ACTION_CW_CHAT = 24          # ACTION_OPT_CW_CHAT
TOLERANCE_MS = 12            # one 10ms scheduler tick, plus a little

# The letter 'A' is dot-dash: the shortest thing that pins down both element lengths and
# the gap between them. Key '2' types it (T9: "ABC2", first press).
LETTER, LETTER_KEY, PATTERN = "A", "2", ".-"

WPMS = [10, 15, 25]


def rows():
    out = []
    with open(KEY_LOG) as f:
        for line in f.read().split():
            try:
                at, state = line.split(",")
                out.append((int(at), state == "1"))
            except ValueError:
                pass          # a partially written line; it will be there next read
    return out


def boot(wpm):
    """A fresh radio each time: the PTT one-push session leaves state behind."""
    subprocess.run([sys.executable, os.path.join(HERE, "make_flash_image.py"),
                    "--out-dir", SANDBOX], check=True, capture_output=True)
    env = dict(os.environ, FLASH_IMAGE=FLASH_IMAGE, UVK5_CW_KEYLOG=KEY_LOG)
    result = subprocess.run([os.path.join(HERE, "dev.sh"), "--restart", "--no-viewer",
                             "--no-build"], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"could not start the simulator:\n{result.stdout}\n{result.stderr}")

    mon = uvctl.Monitor()
    if not uvctl.wait_ready(mon):
        sys.exit("radio never settled")
    base = uvctl.symbol("gEeprom")
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t', 'CW_WPM'):08X} {wpm}")
    # Bind a side key to the CW chat screen so the whole thing is driven from the keypad.
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t', 'KEY_1_SHORT_PRESS_ACTION'):08X} {ACTION_CW_CHAT}")
    return mon


def transmit(mon):
    """Open CW chat, type the letter, and hit PTT. Returns the keyed/unkeyed durations."""
    uvctl.press(["SIDE1"], delay=1.2)          # CW chat screen
    uvctl.press([LETTER_KEY], delay=1.8)       # T9 -> the letter
    before = len(rows())

    # PTT cannot be injected over serial (the firmware blocks it), so pull the real line.
    # Hold it until the message is sent: releasing early cuts the transmission off in the
    # middle of an element, which looks like a timing bug but is the test's own fault.
    mon.command("ptt_press")
    tx_state = uvctl.symbol("tx_state")
    deadline = time.time() + 20
    time.sleep(1.0)
    while time.time() < deadline:
        if mon.read_bytes(tx_state, 1)[0] == 0:   # CW_TX_IDLE: the message is done
            break
        time.sleep(0.3)
    time.sleep(0.5)
    mon.command("ptt_release")
    time.sleep(1)

    edges = rows()[before:]
    return [(edges[i][0] - edges[i - 1][0], edges[i - 1][1])
            for i in range(1, len(edges))]


def check(wpm):
    print(f"  {wpm:2d} WPM  ", end="", flush=True)
    mon = boot(wpm)
    intervals = transmit(mon)
    mon.close()

    dot = 1200 // wpm
    # Arming toggles the PA and the mute a few ms apart; a real element is never that
    # short, so anything under a third of a dot is not an element.
    elements = [(ms, keyed) for ms, keyed in intervals if ms > dot // 3]
    keyed = [ms for ms, on in elements if on]
    gaps = [ms for ms, on in elements if not on]

    if len(keyed) < 2:
        print(f"FAIL — expected 2 elements for '{LETTER}' ({PATTERN}), got {len(keyed)}: {keyed}")
        return False

    sent = "".join("-" if ms > 2 * dot else "." for ms in keyed[:2])
    if sent != PATTERN:
        print(f"FAIL — sent '{sent}', expected '{PATTERN}' for '{LETTER}'")
        return False

    problems = []
    if abs(keyed[0] - dot) > TOLERANCE_MS:
        problems.append(f"dot {keyed[0]}ms, expected {dot}ms")
    if abs(keyed[1] - 3 * dot) > TOLERANCE_MS:
        problems.append(f"dash {keyed[1]}ms, expected {3 * dot}ms")
    if gaps and abs(gaps[0] - dot) > TOLERANCE_MS:
        problems.append(f"element gap {gaps[0]}ms, expected {dot}ms")

    ratio = keyed[1] / keyed[0]
    if abs(ratio - 3.0) > 0.25:
        problems.append(f"dash/dot = {ratio:.2f}, expected 3.00")

    if problems:
        print("FAIL — " + "; ".join(problems))
        return False

    print(f"ok — '{PATTERN}'  dot {keyed[0]}ms  dash {keyed[1]}ms  ratio {ratio:.2f}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="only this WPM")
    args = ap.parse_args()

    speeds = [w for w in WPMS if not args.only or args.only == str(w)]
    if not speeds:
        sys.exit(f"no speed matches '{args.only}'")

    subprocess.run(["cmake", "--build", "--preset", "Fusion", "-j"],
                   check=True, capture_output=True)

    failures = [w for w in speeds if not check(w)]

    print()
    if failures:
        print(f"{len(failures)} of {len(speeds)} speeds sent bad Morse: "
              + ", ".join(f"{w} WPM" for w in failures))
        return 1
    print(f"all {len(speeds)} speeds keyed correct Morse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
