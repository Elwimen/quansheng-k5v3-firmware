#!/usr/bin/env python3
"""Render a UV-K5 framebuffer dump (1024 bytes = 8 pages x 128 cols, LSB=top)
into a PNG. Reads Renode `sysbus ReadBytes` hex from stdin (0xAB, 0xCD, ...)."""
import sys, re
from PIL import Image

data = bytes(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", sys.stdin.read()))
W, H, SCALE = 128, 64, 6
img = Image.new("RGB", (W * SCALE, H * SCALE), (10, 20, 10))
px = img.load()
for x in range(W):
    for y in range(H):
        page = y // 8
        idx = page * W + x
        if idx < len(data) and (data[idx] >> (y % 8)) & 1:
            for dx in range(SCALE):
                for dy in range(SCALE):
                    px[x * SCALE + dx, y * SCALE + dy] = (120, 255, 120)
out = sys.argv[1] if len(sys.argv) > 1 else "screen.png"
img.save(out)
print("wrote", out, "from", len(data), "bytes")
