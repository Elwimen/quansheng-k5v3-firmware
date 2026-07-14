#!/usr/bin/env python3
"""Does a setting the user changes actually survive a power cycle?

    ./sim/test_persist.py
    ./sim/test_persist.py --only cw

Each case changes a setting through the real UI (keypresses into the menu, exactly as a
user would), then kills the simulator and cold-starts it, and asserts the firmware reads
the value back. Nothing is stubbed: the value goes through settings.c ->
eeprom_compat.c -> PY25Q16_WriteBuffer -> SPI2 -> the flash image on disk, and comes back
the same way.

This is the regression that hardware would otherwise catch weeks later: a refactor of the
settings layout or the flash driver that quietly stops saving, and the radio forgets
everything the moment its battery comes out.

Runs on its own throwaway radio, so it never touches the one you have configured.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.environ.get("TMPDIR", "/tmp"), "uvk5-sim", "persist")
FLASH_IMAGE = os.path.join(SANDBOX, "spi_PY25Q16.bin")

# menu entry -> (gEeprom field, width, presses to change it, expected value afterwards)
# The defaults come from settings.c, which is also what a factory-fresh image yields.
CASES = {
    "cw_wpm": {
        "menu": "CWSpd",
        "field": ("CW_WPM", 1),
        "flash": 0x00A170,          # settings.c writes the CW block here
        "keys": ["UP"],             # 15 -> 16
        "expect": 16,
    },
    # CWRHst is the only CW setting kept in CW_FLAGS (CW_FLAG_RECALL_HISTORY, bit 0).
    # Note the flags byte has no validation on load, so an erased radio starts at 0xFF
    # with every flag bit set -- DOWN clears the bit, UP would have nothing to do.
    "cw_flags": {
        "menu": "CWRHst",
        "field": ("CW_FLAGS", 1),
        "flash": 0x00A173,
        "keys": ["DOWN"],
        "expect": 0xFE,
    },
    "squelch": {
        "menu": "Sql",
        "field": ("SQUELCH_LEVEL", 1),
        "flash": None,              # in the settings block; the field check is the point
        "keys": ["UP"],
        "expect": None,
    },
}


def fresh_radio():
    """A factory-fresh radio, with persistence on -- that is the thing under test."""
    subprocess.run([sys.executable, os.path.join(HERE, "make_flash_image.py"),
                    "--out-dir", SANDBOX], check=True, capture_output=True)
    return boot()


def boot():
    """Cold-start the simulator on the sandbox image: a real power cycle."""
    env = dict(os.environ, FLASH_IMAGE=FLASH_IMAGE)
    result = subprocess.run([os.path.join(HERE, "dev.sh"), "--restart", "--no-viewer",
                             "--no-build"],
                            env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"could not start the simulator:\n{result.stdout}\n{result.stderr}")
    mon = uvctl.Monitor()
    if not uvctl.wait_ready(mon):
        sys.exit("radio never settled")
    return mon


def run_case(name, case):
    print(f"  {name:10s}", end=" ", flush=True)

    mon = fresh_radio()
    field, width = case["field"]
    before = uvctl.read_setting(mon, field, width)

    # Change it the way a user does: walk the menu, open the entry, change it, accept.
    uvctl.menu_goto(mon, case["menu"])
    uvctl.press(["MENU"], delay=0.5)          # into the submenu
    time.sleep(0.5)
    uvctl.press(case["keys"], delay=0.5)      # change the value
    time.sleep(0.5)
    uvctl.press(["MENU"], delay=0.5)          # accept -> the firmware saves
    time.sleep(3)                             # let the write reach the flash

    changed = uvctl.read_setting(mon, field, width)
    if changed == before:
        print(f"FAIL — {field} did not change in RAM (still {before})")
        mon.close()
        return False

    # It must be on disk, not just in the emulated part's RAM.
    if case["flash"] is not None:
        with open(FLASH_IMAGE, "rb") as f:
            f.seek(case["flash"])
            on_disk = f.read(width)[0]
        if on_disk != changed:
            print(f"FAIL — {field}={changed} in RAM but flash 0x{case['flash']:06X} "
                  f"holds {on_disk} (the save never reached the external flash)")
            mon.close()
            return False
    mon.close()

    # The real test: pull the battery.
    mon = boot()
    after = uvctl.read_setting(mon, field, width)
    mon.close()

    if after != changed:
        print(f"FAIL — set {field}={changed}, but after a power cycle the radio "
              f"came back with {after}")
        return False

    expected = case["expect"]
    if expected is not None and changed != expected:
        print(f"FAIL — expected {field}={expected}, got {changed}")
        return False

    print(f"ok — {field}: {before} -> {changed}, survived a power cycle")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="run cases whose name contains this")
    args = ap.parse_args()

    cases = {k: v for k, v in CASES.items() if not args.only or args.only in k}
    if not cases:
        sys.exit(f"no case matches '{args.only}'")

    # Build once; each case cold-starts the simulator but not the compiler.
    subprocess.run(["cmake", "--build", "--preset", "Fusion", "-j"],
                   check=True, capture_output=True)

    failures = [name for name, case in cases.items() if not run_case(name, case)]

    print()
    if failures:
        print(f"{len(failures)} of {len(cases)} failed: {', '.join(failures)}")
        return 1
    print(f"all {len(cases)} settings survived a power cycle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
