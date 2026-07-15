#!/usr/bin/env python3
"""Does the CW receiver reject noise instead of decoding it as a stream of E's?

    ./sim/test_cw_rx_noise.py

On the air, REG_6F is a *broadband* amplitude with no tone selectivity, so band noise and
QRN poke above the decoder's threshold. Without a gate the timer reads each blip as a
one-dit element -- E after E after E -- which is exactly what the radio did on first
contact with real signals. The sim models this now: SetRxNoise(level) adds white noise and
bursts to REG_6F, and the BK4819 squelch interrupt (REG_02/REG_0C) tracks the *clean*
carrier, the way an RSSI-based squelch does.

Two things are asserted:

  A. Empty (noisy) channel, no carrier: the decoder must stay silent. The squelch never
     opens, so the arming gate never lets the decoder start -- history stays empty. This is
     the bug that was reported; at level 12 the bursts would sail through the millisecond
     debounce, so only the squelch gate can hold this line.

  B. Real keyed signal under moderate noise: it must still decode. The gates must reject
     noise without becoming deaf to a genuine station.

Heavy noise riding *on top of* a signal (SNR near 0 dB) still corrupts copy -- that is the
structural ceiling of an envelope detector with no tone filter, not a decoder bug -- so B
uses a moderate level where copy is expected to be clean.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.environ.get("TMPDIR", "/tmp"), "uvk5-sim", "cwrx")
FLASH_IMAGE = os.path.join(SANDBOX, "spi_PY25Q16.bin")

ACTION_CW_CHAT = 24
DISPLAY_CW_CHAT = 5
WPM = 20
TEXT = "TEST"
NOISE_SILENT = 12   # empty channel: harsh, bursts survive the debounce -> squelch must hold
NOISE_COPY = 4      # over a signal: moderate, copy expected clean


def rx_text(mon):
    raw = mon.read_bytes(uvctl.symbol("cw_history"), 21)
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def hist_count(mon):
    return mon.read_bytes(uvctl.symbol("cw_history_count"), 1)[0]


def boot():
    subprocess.run([sys.executable, os.path.join(HERE, "make_flash_image.py"),
                    "--out-dir", SANDBOX], check=True, capture_output=True)
    env = dict(os.environ, FLASH_IMAGE=FLASH_IMAGE)
    result = subprocess.run([os.path.join(HERE, "dev.sh"), "--restart", "--no-viewer",
                             "--no-build"], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"could not start the simulator:\n{result.stdout}\n{result.stderr}")

    mon = uvctl.Monitor()
    if not uvctl.wait_ready(mon):
        sys.exit("radio never settled")
    base = uvctl.symbol("gEeprom")
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t', 'CW_WPM'):08X} {WPM}")
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t', 'KEY_1_SHORT_PRESS_ACTION'):08X} {ACTION_CW_CHAT}")

    screen = uvctl.symbol("gScreenToDisplay")
    for _ in range(3):
        uvctl.press(["SIDE1"], delay=1.2)
        if mon.read_bytes(screen, 1)[0] == DISPLAY_CW_CHAT:
            break
    if mon.read_bytes(screen, 1)[0] != DISPLAY_CW_CHAT:
        sys.exit("never reached the CW chat screen")
    return mon


def main():
    subprocess.run(["cmake", "--build", "--preset", "Fusion", "-j"],
                   check=True, capture_output=True)

    ok = True

    # A. Noisy but empty channel -- must stay silent.
    print("  empty noisy channel  ", end="", flush=True)
    mon = boot()
    mon.command(f"cw_rx_noise {NOISE_SILENT}")
    time.sleep(6)
    count = hist_count(mon)
    text = rx_text(mon)
    mon.close()
    if count == 0:
        print(f"ok — nothing decoded from noise (level {NOISE_SILENT})")
    else:
        print(f"FAIL — decoded {text!r} from noise alone (history_count={count})")
        ok = False

    # B. Real signal under moderate noise -- must still copy.
    print("  signal under noise   ", end="", flush=True)
    mon = boot()
    mon.command(f"cw_rx_noise {NOISE_COPY}")
    mon.command(f'cw_rx_send "{TEXT}" {WPM}')
    time.sleep(len(TEXT) * (1200 / WPM) * 12 / 1000 + 6)
    text = rx_text(mon).strip()
    mon.close()
    if text == TEXT:
        print(f"ok — copied {text!r} through noise (level {NOISE_COPY})")
    else:
        print(f"FAIL — sent {TEXT!r}, copied {text!r} (level {NOISE_COPY})")
        ok = False

    print()
    if ok:
        print("CW receiver rejects noise and still copies a real signal")
        return 0
    print("CW noise handling regressed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
