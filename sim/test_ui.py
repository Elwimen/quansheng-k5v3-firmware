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
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(HERE, "golden")
FAILED_DIR = os.path.join(HERE, "golden", "failed")

# The simulator mirrors the firmware's flash writes back into its image, so the radio you
# develop against keeps its configuration. The tests must not run against that: the
# goldens are of a factory-fresh radio, and a test must never write to your channels. So
# they generate their own images and boot the simulator on those.
SANDBOX = os.path.join(os.environ.get("TMPDIR", "/tmp"), "uvk5-sim", "test")
FLASH_IMAGE = os.path.join(SANDBOX, "spi_PY25Q16.bin")


def start_pristine_sim():
    subprocess.run([sys.executable, os.path.join(HERE, "make_flash_image.py"),
                    "--out-dir", SANDBOX], check=True, capture_output=True)
    env = dict(os.environ, FLASH_IMAGE=FLASH_IMAGE)
    result = subprocess.run([os.path.join(HERE, "dev.sh"), "--restart", "--no-viewer"],
                            env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"could not start the simulator:\n{result.stdout}\n{result.stderr}")

    # Turn persistence off for the run. The firmware writes to its flash as it boots, and
    # with write-through on it would dirty the very image each case re-seeds from, so the
    # radio would drift out from under the goldens as the suite ran. With no backing file
    # the writes stay in emulated RAM, and every reboot starts from the pristine image.
    mon = uvctl.Monitor()
    mon.command('spiFlash ImagePath ""')
    mon.close()

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
    """A cold power cycle, so no case can inherit the state of the one before it.

    A plain `machine Reset` is not enough: Renode's MappedMemory.Reset() is a no-op, so
    both the SRAM and the emulated flash keep whatever the last case left in them, and
    the radio drifts out from under the goldens as the suite runs. Wipe the RAM and
    re-seed the flash from the pristine image by hand.
    """
    mon.command("pause")
    mon.command("machine Reset")
    mon.command("sysbus ZeroRange 0x20000000 0x4000")            # SRAM
    mon.command(f"sysbus LoadBinary @{FLASH_IMAGE} 0x90000000")  # flash
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

    print("  starting a factory-fresh radio (your configured one is left alone)")
    start_pristine_sim()

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
