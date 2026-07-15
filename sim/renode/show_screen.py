# -*- coding: utf-8 -*-
#
# Monitor command `show_screen`: render the UV-K5 128x64 LCD as Unicode block-art
# directly in the Renode monitor, by reading the firmware framebuffer from RAM
# (gStatusLine = top page, gFrameBuffer = pages 1..7; byte = 8 vertical px, LSB top).
#
#   (monitor) show_screen
#
# NB: Renode's Python is IronPython 2.7, which requires the utf-8 coding header
# above before any non-ASCII characters (the block glyphs).

def mc_show_screen():
    bus = monitor.Machine.SystemBus
    cpu = None
    for c in bus.GetCPUs():
        cpu = c
        break

    def sym(name):
        ok, addrs = bus.TryGetAllSymbolAddresses(name, cpu)
        if not ok:
            return None
        for a in addrs:
            return a
        return None

    sl = sym("gStatusLine")
    fb = sym("gFrameBuffer")
    if sl is None or fb is None:
        print("show_screen: gStatusLine/gFrameBuffer not found (is the firmware ELF loaded?)")
        return

    data = bytearray(bus.ReadBytes(sl, 128, True)) + bytearray(bus.ReadBytes(fb, 896, True))

    def px(x, y):
        idx = (y // 8) * 128 + x
        if idx >= len(data):
            return 0
        return (data[idx] >> (y % 8)) & 1

    # index = top*2 + bot: 0=space 1=lower-half 2=upper-half 3=full block
    glyphs = u" ▄▀█"
    print("+" + "-" * 128 + "+")
    for ty in range(32):
        row = []
        for x in range(128):
            top = px(x, ty * 2)
            bot = px(x, ty * 2 + 1)
            row.append(glyphs[top * 2 + bot])
        print(u"|" + u"".join(row) + u"|")
    print("+" + "-" * 128 + "+")
