#!/usr/bin/env python3
"""Does the live CW decoder work on the MAIN screen, and does CWHold control how long a
decode lingers there?

    ./sim/test_cw_main.py

With CWMon=Main the receiver decodes in the background while the radio sits on its normal VFO
screen. A confirmed decode is held on the centre line for CWHold seconds after the sender
stops. This drives the sim from the main screen (never opening the CW chat window), keys a
message, and checks: (1) it decoded and was confirmed as Morse, and (2) the hold timer was
armed to CWHold seconds -- proving the setting takes effect.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.environ.get("TMPDIR", "/tmp"), "uvk5-sim", "cwmain")
FLASH_IMAGE = os.path.join(SANDBOX, "spi_PY25Q16.bin")
WPM = 20


def boot(mon_scope, hold_s):
    subprocess.run([sys.executable, os.path.join(HERE, "make_flash_image.py"),
                    "--out-dir", SANDBOX], check=True, capture_output=True)
    r = subprocess.run([os.path.join(HERE, "dev.sh"), "--restart", "--no-viewer", "--no-build"],
                       env=dict(os.environ, FLASH_IMAGE=FLASH_IMAGE), capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"sim failed to start:\n{r.stdout}\n{r.stderr}")
    mon = uvctl.Monitor()
    if not uvctl.wait_ready(mon):
        sys.exit("radio never settled")
    base = uvctl.symbol("gEeprom")
    fa = base + uvctl.field_offset("EEPROM_Config_t", "CW_FLAGS")
    cur = mon.read_bytes(fa, 1)[0]
    flags = (cur & ~0x06 & ~0xF8) | ((mon_scope & 3) << 1) | ((hold_s & 0x1F) << 3)
    mon.command(f"sysbus WriteByte 0x{fa:08X} {flags}")
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t','CW_WPM'):08X} {WPM}")
    return mon


def run_case(mon_scope, hold_s):
    mon = boot(mon_scope, hold_s)
    S = uvctl.symbols()
    def rd(n, sz=1):
        return int.from_bytes(mon.read_bytes(S[n], sz), "little")

    time.sleep(1.2)                       # let the threshold self-calibrate on the quiet channel
    assert rd("gScreenToDisplay") == 0, "should be on the main screen"
    thr = rd("rx_threshold", 2)

    mon.command(f'cw_rx_send "CQ DE N0CALL" {WPM}')
    detected, peak_hold = False, 0
    for _ in range(120):
        time.sleep(0.2)
        if rd("rx_detected"):
            detected = True
        peak_hold = max(peak_hold, rd("rx_show_ms", 2))
        if detected and rd("rx_state") == 0 and rd("rx_show_ms", 2) == 0:
            break
    hist = rd("cw_history_count")
    mon.close()
    return dict(thr=thr, detected=detected, hist=hist, peak_hold=peak_hold)


def main():
    subprocess.run(["cmake", "--build", "--preset", "Fusion", "-j"],
                   check=True, capture_output=True)
    ok = True

    print("  CWMon=Main, decode + confirm ", end="", flush=True)
    a = run_case(mon_scope=1, hold_s=6)
    if a["detected"] and a["hist"] > 0 and a["thr"] > 0:
        print(f"ok (thr {a['thr']}, {a['hist']} line(s), confirmed as Morse)")
    else:
        print(f"FAIL {a}"); ok = False

    # Hold time: the peak hold timer should equal CWHold seconds (within one 10ms tick region).
    for hold in (3, 8):
        print(f"  CWHold={hold}s hold timer      ", end="", flush=True)
        r = run_case(mon_scope=1, hold_s=hold)
        want = hold * 1000
        if r["detected"] and abs(r["peak_hold"] - want) <= 300:
            print(f"ok (peak {r['peak_hold']}ms ~= {want}ms)")
        else:
            print(f"FAIL peak {r['peak_hold']}ms, wanted ~{want}ms ({r})"); ok = False

    print()
    if ok:
        print("main-screen CW decoder works and CWHold controls the display hold")
        return 0
    print("main-screen CW decoder regressed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
