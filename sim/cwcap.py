#!/usr/bin/env python3
"""Record the CW chat screen decoding a message, as a GIF (+ optional MP4).

    ./sim/cwcap.py --text "CQ N0CALL" --wpm 20 --noise 4 --out /tmp/cw.gif

Boots the sim on the CW chat screen, keys a Morse signal at the receiver, and grabs
framebuffer frames while it decodes, then stitches them into a scaled GIF so each UI
iteration can be watched. Frames are captured in wall-clock time (the sim runs slower than
real), so the GIF shows the decode progressing.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvctl

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(os.environ.get("TMPDIR", "/tmp"), "uvk5-sim", "cwcap")
FLASH_IMAGE = os.path.join(SANDBOX, "spi_PY25Q16.bin")
ACTION_CW_CHAT = 24
DISPLAY_CW_CHAT = 5


def boot(wpm):
    subprocess.run([sys.executable, os.path.join(HERE, "make_flash_image.py"),
                    "--out-dir", SANDBOX], check=True, capture_output=True)
    env = dict(os.environ, FLASH_IMAGE=FLASH_IMAGE)
    r = subprocess.run([os.path.join(HERE, "dev.sh"), "--restart", "--no-viewer", "--no-build"],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"sim failed to start:\n{r.stdout}\n{r.stderr}")
    mon = uvctl.Monitor()
    if not uvctl.wait_ready(mon):
        sys.exit("radio never settled")
    base = uvctl.symbol("gEeprom")
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t', 'CW_WPM'):08X} {wpm}")
    mon.command(f"sysbus WriteByte 0x{base + uvctl.field_offset('EEPROM_Config_t', 'KEY_1_SHORT_PRESS_ACTION'):08X} {ACTION_CW_CHAT}")
    screen = uvctl.symbol("gScreenToDisplay")
    for _ in range(3):
        uvctl.press(["SIDE1"], delay=1.2)
        if mon.read_bytes(screen, 1)[0] == DISPLAY_CW_CHAT:
            break
    if mon.read_bytes(screen, 1)[0] != DISPLAY_CW_CHAT:
        sys.exit("never reached the CW chat screen")
    return mon


def frame_image(screen, scale):
    from PIL import Image
    img = Image.new("1", (uvctl.WIDTH, uvctl.HEIGHT))
    px = img.load()
    for x in range(uvctl.WIDTH):
        for y in range(uvctl.HEIGHT):
            px[x, y] = uvctl.pixel(screen, x, y)
    return img.resize((uvctl.WIDTH * scale, uvctl.HEIGHT * scale), Image.NEAREST).convert("P")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default="CQ N0CALL")
    ap.add_argument("--wpm", type=int, default=20)
    ap.add_argument("--noise", type=int, default=0)
    ap.add_argument("--compose", default="", help="preload the compose buffer (shows counter)")
    ap.add_argument("--out", default="/tmp/cw.gif")
    ap.add_argument("--seconds", type=float, default=30.0, help="wall-clock capture window")
    ap.add_argument("--interval", type=float, default=0.15, help="grab interval (s)")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--fps", type=int, default=12, help="GIF playback fps")
    ap.add_argument("--mp4", action="store_true", help="also write <out>.mp4")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()

    if not args.no_build:
        subprocess.run(["cmake", "--build", "--preset", "Fusion", "-j"],
                       check=True, capture_output=True)

    mon = boot(args.wpm)
    if args.compose:
        addr = uvctl.symbol("cw_compose")
        for k, ch in enumerate(args.compose):
            mon.command(f"sysbus WriteByte 0x{addr + k:08X} {ord(ch)}")
        mon.command(f"sysbus WriteByte 0x{addr + len(args.compose):08X} 0")
    if args.noise > 0:
        mon.command(f"cw_rx_noise {args.noise}")
    mon.command(f'cw_rx_send "{args.text}" {args.wpm}')

    print(f"capturing ~{args.seconds}s at {args.interval}s intervals...")
    frames, last = [], None
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        t0 = time.time()
        try:
            screen = uvctl.grab(mon)
        except Exception:
            continue
        key = bytes(screen)
        if key != last:                 # keep only frames that changed
            frames.append(frame_image(screen, args.scale))
            last = key
        dt = args.interval - (time.time() - t0)
        if dt > 0:
            time.sleep(dt)
    mon.close()

    if not frames:
        sys.exit("no frames captured")
    # Hold the final frame a moment.
    frames += [frames[-1]] * args.fps
    dur = int(1000 / args.fps)
    frames[0].save(args.out, save_all=True, append_images=frames[1:],
                   duration=dur, loop=0, optimize=True)
    print(f"wrote {args.out} ({len(frames)} frames, {args.scale}x, {args.fps}fps)")

    if args.mp4:
        mp4 = args.out.rsplit(".", 1)[0] + ".mp4"
        subprocess.run(["ffmpeg", "-y", "-i", args.out, "-movflags", "faststart",
                        "-pix_fmt", "yuv420p", mp4], capture_output=True)
        print(f"wrote {mp4}")


if __name__ == "__main__":
    sys.exit(main())
