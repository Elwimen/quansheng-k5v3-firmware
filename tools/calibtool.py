#!/usr/bin/env python3
"""Decode, compare and edit a UV-K5/K1 calibration dump (a 512-byte calib.dat from
`uvflash dump-calib`).

The field layout is read from tools/calib_layout.json, generated from the single
source of truth App/driver/spi_flash_layout.h by tools/gen_flash_layout.py — so it
can never drift from the firmware. Each field carries a role:
  *  critical  RF calibration (TX power, crystal trim, battery ref) — a wrong value
               mis-tunes the radio (off power / off frequency / wrong battery).
     cal       front-end thresholds (squelch/RSSI/S-meter/VOX/mic) — per-unit.
  ~  volatile  operator setting stored in the cal area (volumeGain/dacGain) — it
               changes with normal use, so a diff here is expected and harmless.

    calibtool.py decode a.dat
    calibtool.py diff a.dat b.dat
    calibtool.py get  a.dat txp[2].hi.center
    calibtool.py set  a.dat volumeGain 20 [--out b.dat]
"""
import argparse
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAYOUT_PATH = HERE / "calib_layout.json"
FMT = {"u8": "<B", "u16": "<H", "u32": "<I", "i8": "<b", "i16": "<h", "i32": "<i"}
ROLE_TAG = {"critical": "*", "volatile": "~", "cal": " "}


def load_layout():
    if not LAYOUT_PATH.exists():
        sys.exit(f"missing {LAYOUT_PATH} — run tools/gen_flash_layout.py")
    return json.loads(LAYOUT_PATH.read_text())


def read_calib(path):
    data = Path(path).read_bytes()
    lay = load_layout()
    if len(data) < lay["size"]:
        sys.exit(f"{path}: {len(data)} bytes, need >= {lay['size']} for a calib block")
    return data, lay


def _field(lay, name):
    f = next((x for x in lay["fields"] if x["name"] == name), None)
    if not f:
        sys.exit(f"no calib field '{name}' (see: calibtool.py decode <file>)")
    return f


def value_of(data, f):
    fmt, sz, o, n = FMT[f["ctype"]], f["size"], f["offset"], f["count"]
    vals = [struct.unpack_from(fmt, data, o + i * sz)[0] for i in range(n)]
    return vals[0] if n == 1 else vals


def parse(data, lay=None):
    lay = lay or load_layout()
    return {f["name"]: value_of(data, f) for f in lay["fields"]}


def decode_text(data, lay=None):
    lay = lay or load_layout()
    out = [f"calibration block: {lay['struct']} (0x{lay['size']:X} bytes)",
           "  (* = RF-critical, ~ = volatile operator setting)"]
    for f in lay["fields"]:
        out.append(f"  {ROLE_TAG[f['role']]} {f['name']:26s} @0x{f['offset']:03X}  "
                   f"{f['ctype']}  {value_of(data, f)}")
    return "\n".join(out)


def diff_rows(a, b, lay=None):
    lay = lay or load_layout()
    rows = []
    for f in lay["fields"]:
        va, vb = value_of(a, f), value_of(b, f)
        if va != vb:
            rows.append((f["name"], f["role"], f["offset"], va, vb))
    return rows


def diff_text(a, b, lay=None):
    rows = diff_rows(a, b, lay)
    if not rows:
        return "identical (all calibration fields match)"
    crit = [r for r in rows if r[1] == "critical"]
    vol = [r for r in rows if r[1] == "volatile"]
    out = [f"{len(rows)} field(s) differ  "
           f"({len(crit)} RF-critical, {len(vol)} volatile, {len(rows)-len(crit)-len(vol)} other):"]
    for name, role, off, va, vb in rows:
        out.append(f"  {ROLE_TAG[role]} {name:26s} @0x{off:03X}   A={va}   B={vb}")
    if crit:
        out.append("\nWARNING: RF-critical fields differ — these are different units, "
                   "or one is miscalibrated. Do NOT cross-restore between radios.")
    elif vol and len(rows) == len(vol):
        out.append("\n(only volatile settings differ — same radio, e.g. the volume knob moved.)")
    return "\n".join(out)


def set_field(data, name, value, lay=None):
    """Return a NEW bytes with `name` set to `value` (scalar or list), range-checked."""
    lay = lay or load_layout()
    f = _field(lay, name)
    fmt, sz, o, n = FMT[f["ctype"]], f["size"], f["offset"], f["count"]
    vals = value if isinstance(value, list) else [value]
    if len(vals) != n:
        raise ValueError(f"{name} needs {n} value(s), got {len(vals)}")
    signed = f["ctype"].startswith("i")
    lo, hi = (-(1 << (sz * 8 - 1)), (1 << (sz * 8 - 1)) - 1) if signed else (0, (1 << (sz * 8)) - 1)
    b = bytearray(data)
    for i, v in enumerate(vals):
        v = int(v)
        if not lo <= v <= hi:
            raise ValueError(f"{name}[{i}]={v} out of range [{lo},{hi}] for {f['ctype']}")
        struct.pack_into(fmt, b, o + i * sz, v)
    return bytes(b), f


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decode"); p.add_argument("file")
    p = sub.add_parser("diff"); p.add_argument("a"); p.add_argument("b")
    p = sub.add_parser("get"); p.add_argument("file"); p.add_argument("field")
    p = sub.add_parser("set"); p.add_argument("file"); p.add_argument("field")
    p.add_argument("value", nargs="+"); p.add_argument("--out")
    a = ap.parse_args()

    if a.cmd == "decode":
        data, lay = read_calib(a.file)
        print(decode_text(data, lay))
    elif a.cmd == "diff":
        da, lay = read_calib(a.a)
        db, _ = read_calib(a.b)
        print(diff_text(da, db, lay))
    elif a.cmd == "get":
        data, lay = read_calib(a.file)
        print(value_of(data, _field(lay, a.field)))
    elif a.cmd == "set":
        data, lay = read_calib(a.file)
        vals = [int(v, 0) for v in a.value]
        new, f = set_field(data, a.field, vals if len(vals) > 1 else vals[0], lay)
        out = a.out or a.file
        Path(out).write_bytes(new)
        print(f"set {a.field} = {value_of(new, f)}  -> {out}")


if __name__ == "__main__":
    main()
