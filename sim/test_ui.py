#!/usr/bin/env python3
"""Golden-screen tests: drive the simulated radio and compare the LCD, pixel for pixel.

    ./sim/dev.sh          # sim must be running
    ./sim/test_ui.py                  # run the suite
    ./sim/test_ui.py --update         # re-record the goldens (review the diff!)
    ./sim/test_ui.py --only menu      # a subset

Each case reboots the radio first, so a case cannot inherit the state of the one
before it. Goldens live in sim/golden/*.png; a failure writes the actual screen and a
diff image next to them so you can see what moved.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(HERE, "golden")
FAILED_DIR = os.path.join(HERE, "golden", "failed")

# name -> keys pressed from a freshly booted radio.
CASES = {
    "main":            [],
    "menu":            ["MENU"],
    "menu_scrolled":   ["MENU", "DOWN", "DOWN"],
    "menu_step":       ["MENU", "MENU"],
    "back_to_main":    ["MENU", "EXIT"],
    "vfo_frequency":   ["1"],
}


def reboot(mon):
    """A known starting point: reset, re-seed the stores, wait for the screen to settle."""
    mon.command("pause")
    mon.command("machine Reset")
    mon.command("sysbus LoadBinary @sim/data/spi_PY25Q16.bin 0x90000000")
    mon.command("sysbus LoadBinary @sim/data/eeprom.bin 0x90200000")
    mon.command("start")
    if not uvctl.wait_ready(mon):
        sys.exit("radio never settled after reset")


def to_bits(screen):
    return [uvctl.pixel(screen, x, y) for y in range(uvctl.HEIGHT) for x in range(uvctl.WIDTH)]


def load_golden(path):
    from PIL import Image
    img = Image.open(path).convert("L").resize((uvctl.WIDTH, uvctl.HEIGHT))
    px = img.load()
    return [1 if px[x, y] > 127 else 0
            for y in range(uvctl.HEIGHT) for x in range(uvctl.WIDTH)]


def save_diff(actual, golden, path):
    """Red = pixels that changed, so a regression is obvious at a glance."""
    from PIL import Image
    img = Image.new("RGB", (uvctl.WIDTH, uvctl.HEIGHT))
    px = img.load()
    for i, (a, g) in enumerate(zip(actual, golden)):
        x, y = i % uvctl.WIDTH, i // uvctl.WIDTH
        if a != g:
            px[x, y] = (255, 0, 0)
        else:
            px[x, y] = (255, 255, 255) if a else (0, 0, 0)
    img.resize((uvctl.WIDTH * 4, uvctl.HEIGHT * 4), Image.NEAREST).save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="re-record goldens")
    ap.add_argument("--only", help="run cases whose name contains this")
    args = ap.parse_args()

    os.makedirs(GOLDEN_DIR, exist_ok=True)
    cases = {k: v for k, v in CASES.items() if not args.only or args.only in k}
    if not cases:
        sys.exit(f"no case matches '{args.only}'")

    mon = uvctl.Monitor()
    failures = []
    for name, keys in cases.items():
        print(f"  {name:18s}", end=" ", flush=True)
        reboot(mon)
        if keys:
            uvctl.press(keys)
            time.sleep(1.0)
        screen = uvctl.grab(mon)
        golden_path = os.path.join(GOLDEN_DIR, f"{name}.png")

        if args.update or not os.path.exists(golden_path):
            uvctl.as_png(screen, golden_path)
            print("recorded" if args.update else "recorded (new)")
            continue

        actual, golden = to_bits(screen), load_golden(golden_path)
        wrong = sum(a != g for a, g in zip(actual, golden))
        if wrong == 0:
            print("ok")
        else:
            os.makedirs(FAILED_DIR, exist_ok=True)
            uvctl.as_png(screen, os.path.join(FAILED_DIR, f"{name}.png"))
            save_diff(actual, golden, os.path.join(FAILED_DIR, f"{name}.diff.png"))
            print(f"FAIL — {wrong} px differ from golden/{name}.png")
            print(f"       actual + diff written to golden/failed/{name}*.png")
            failures.append(name)
    mon.close()

    print()
    if failures:
        print(f"{len(failures)} of {len(cases)} failed: {', '.join(failures)}")
        return 1
    print(f"all {len(cases)} screens match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
