#!/usr/bin/env python3
"""Capture the CW chat screen decoding a SEQUENCE of received messages (or one long one),
so history scrolling, word-wrap and the status line can be checked end-to-end.

    ./sim/cwseq.py --texts "CQ CQ|DE N0CALL|GM OM|73 GL" --wpm 20 --out seq.gif
    ./sim/cwseq.py --texts "CQ CQ DE N0CALL PSE K UP 5 = OP TEST" --wpm 22 --out long.gif

Each message is keyed at the receiver; we wait for the decoder to return to idle (rx_state)
before sending the next, grabbing frames throughout.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl
import cwcap


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--texts", required=True, help="messages separated by '|'")
    ap.add_argument("--wpm", type=int, default=20)
    ap.add_argument("--noise", type=int, default=0)
    ap.add_argument("--out", default="/tmp/cwseq.gif")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()

    if not args.no_build:
        import subprocess
        subprocess.run(["cmake", "--build", "--preset", "Fusion", "-j"],
                       check=True, capture_output=True)

    messages = args.texts.split("|")
    mon = cwcap.boot(args.wpm)
    if args.noise > 0:
        mon.command(f"cw_rx_noise {args.noise}")

    rx_state = uvctl.symbol("rx_state")
    frames, last = [], None

    def snap():
        nonlocal last
        try:
            s = uvctl.grab(mon)
        except Exception:
            return
        if bytes(s) != last:
            frames.append(cwcap.frame_image(s, args.scale))
            last = bytes(s)

    def idle():
        return mon.read_bytes(rx_state, 1)[0] == 0

    for msg in messages:
        mon.command(f'cw_rx_send "{msg}" {args.wpm}')
        # wait for it to start
        t0 = time.time()
        while idle() and time.time() - t0 < 8:
            snap(); time.sleep(0.08)
        # capture through the decode until idle for a sustained stretch
        idle_since = None
        t0 = time.time()
        while time.time() - t0 < 40:
            snap()
            if idle():
                idle_since = idle_since or time.time()
                if time.time() - idle_since > 2.0:
                    break
            else:
                idle_since = None
            time.sleep(0.08)
        time.sleep(0.4)

    mon.close()
    if not frames:
        sys.exit("no frames")
    frames += [frames[-1]] * args.fps
    frames[0].save(args.out, save_all=True, append_images=frames[1:],
                   duration=int(1000 / args.fps), loop=0, optimize=True)
    print(f"wrote {args.out} ({len(frames)} frames)")


if __name__ == "__main__":
    sys.exit(main())
