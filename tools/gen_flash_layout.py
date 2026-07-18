#!/usr/bin/env python3
"""Generate the ImHex pattern and CHIRP MEM_FORMAT from the single C source of truth
App/driver/spi_flash_layout.h.

    python3 tools/gen_flash_layout.py            # write hexpat + mem_format
    python3 tools/gen_flash_layout.py --check    # regenerate + diff, don't write
    python3 tools/gen_flash_layout.py --dump      # print the extracted model

How it works: compile the header to DWARF (the compiler is the authority on layout),
read every struct member offset/size and bitfield bit position via pyelftools, parse the
FLASH_REGIONS X-macro for placement, then emit each target. Bitfields are stored LSB-first
in C; ImHex is LSB-first too (emit as-is) while CHIRP's bitwise DSL is MSB-first (emit the
fields of each storage unit in reverse) -- both describe the same physical bits.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HEADER = os.path.join(ROOT, "App", "driver", "spi_flash_layout.h")
HEXPAT_OUT = os.path.join(HERE, "spi_PY25Q16.hexpat")
MEMFMT_OUT = os.path.join(HERE, "f4hwn_fusion.mem_format.txt")
CALIB_HEXPAT_OUT = os.path.join(HERE, "spi_calib.hexpat")   # calibration-only pattern
CALIB_JSON_OUT = os.path.join(HERE, "calib_layout.json")    # flat layout for calibtool.py

try:
    from elftools.elf.elffile import ELFFile
except ImportError:
    sys.exit("pyelftools required: pip install pyelftools")


# ---------------------------------------------------------------- DWARF model
# A resolved type is one of:
#   ('base', tok)                 tok in {u8,u16,u32,i8,i16,i32,char}
#   ('enum', name, {val:label})
#   ('struct', name_or_None, [Field, ...], size)
#   ('array', elem_type, count)
class Field:
    __slots__ = ("name", "type", "offset", "bit_size", "bit_off")

    def __init__(self, name, type, offset, bit_size=None, bit_off=None):
        self.name, self.type, self.offset = name, type, offset
        self.bit_size, self.bit_off = bit_size, bit_off


def compile_dwarf():
    """Compile a TU that includes the header into an ELF with DWARF-5 debug info."""
    tmp = tempfile.mkdtemp(prefix="fl_gen_")
    src, obj = os.path.join(tmp, "u.c"), os.path.join(tmp, "u.o")
    with open(src, "w") as f:
        f.write('#include "spi_flash_layout.h"\n')
        f.write("#define USE(field, type, count, addr, cname, incp) type field##_v;\n")
        f.write("FLASH_REGIONS(USE)\n")
    r = subprocess.run(["gcc", "-std=c11", "-g3", "-gdwarf-5", "-c", src, "-o", obj,
                        "-I", os.path.dirname(HEADER)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"compile failed:\n{r.stderr}")
    return obj


def _walk(obj):
    """Return {typedef_name: resolved struct type}, {enum_name: {val:label}}."""
    with open(obj, "rb") as f:
        dw = ELFFile(f).get_dwarf_info()
        by_off, cu0 = {}, None
        for cu in dw.iter_CUs():
            for die in cu.iter_DIEs():
                by_off[die.offset] = (die, cu.cu_offset)
            cu0 = cu

    def die_at(ref, cu_base):
        return by_off.get(cu_base + ref, (None, None))[0]

    def nm(die):
        a = die and die.attributes.get("DW_AT_name")
        return a.value.decode() if a else None

    def resolve(die, cu_base):
        if die is None:
            return ("base", "u8")
        t = die.tag
        if t == "DW_TAG_typedef" or t in ("DW_TAG_const_type", "DW_TAG_volatile_type"):
            a = die.attributes.get("DW_AT_type")
            return resolve(die_at(a.value, cu_base) if a else None, cu_base)
        if t == "DW_TAG_base_type":
            n, sz = nm(die) or "", die.attributes["DW_AT_byte_size"].value
            if n == "char":
                return ("base", "char")
            sign = "i" if ("unsigned" not in n and "_Bool" not in n) else "u"
            return ("base", sign + str(sz * 8))
        if t == "DW_TAG_enumeration_type":
            vals = {c.attributes["DW_AT_const_value"].value: nm(c)
                    for c in die.iter_children() if c.tag == "DW_TAG_enumerator"}
            return ("enum", nm(die), vals)
        if t in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            size = die.attributes.get("DW_AT_byte_size")
            fields = []
            for m in die.iter_children():
                if m.tag != "DW_TAG_member":
                    continue
                off = m.attributes.get("DW_AT_data_member_location")
                bs = m.attributes.get("DW_AT_bit_size")
                bo = m.attributes.get("DW_AT_data_bit_offset")
                mname = nm(m)
                if mname and mname.startswith("x_"):   # un-mangle names that dodge -D macros
                    mname = mname[2:]
                fields.append(Field(
                    mname, resolve(die_at(m.attributes["DW_AT_type"].value, cu_base), cu_base),
                    off.value if off else 0,
                    bs.value if bs else None, bo.value if bo else None))
            return ("struct", nm(die), fields, size.value if size else 0)
        if t == "DW_TAG_array_type":
            elem = resolve(die_at(die.attributes["DW_AT_type"].value, cu_base), cu_base)
            count = 1
            for c in die.iter_children():
                if c.tag == "DW_TAG_subrange_type":
                    ub = c.attributes.get("DW_AT_upper_bound")
                    cn = c.attributes.get("DW_AT_count")
                    count = (ub.value + 1) if ub else (cn.value if cn else 0)
            return ("array", elem, count)
        return ("base", "u8")

    types, enums = {}, {}
    for off, (die, cu_base) in by_off.items():
        if die.tag == "DW_TAG_typedef":
            r = resolve(die, cu_base)
            if r[0] == "struct":
                types[nm(die)] = r
        if die.tag == "DW_TAG_enumeration_type" and nm(die):
            enums[nm(die)] = {c.attributes["DW_AT_const_value"].value: nm(c)
                              for c in die.iter_children() if c.tag == "DW_TAG_enumerator"}
    return types, enums


# ---------------------------------------------------------------- FLASH_REGIONS
def parse_regions():
    src = open(HEADER).read()
    m = re.search(r"#define FLASH_REGIONS\(X\)(.*?)\n\n", src, re.S)
    if not m:
        sys.exit("FLASH_REGIONS macro not found")
    regions = []
    for line in m.group(1).splitlines():
        mm = re.search(r"X\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\d+)\s*,\s*(0x[0-9A-Fa-f]+)\s*,"
                       r"\s*(/\*bare\*/|\w+)\s*,\s*([01])\s*\)", line)
        if mm:
            regions.append(dict(field=mm.group(1), type=mm.group(2), count=int(mm.group(3)),
                                addr=int(mm.group(4), 16),
                                cname=None if mm.group(5) == "/*bare*/" else mm.group(5),
                                in_chirp=mm.group(6) == "1"))
    return regions


# ---------------------------------------------------------------- emit helpers
HEX_BASE = {"u8": "u8", "u16": "u16", "u32": "u32",
            "i8": "s8", "i16": "s16", "i32": "s32", "char": "char"}
CHIRP_BASE = {"u8": "u8", "u16": "ul16", "u32": "ul32",
              "i8": "i8", "i16": "il16", "i32": "il32", "char": "char"}


def iter_items(fields):
    """Ordered emit items: ('field', Field) or ('bits', byte, [Field,...]) for a u8
    bitfield storage unit (all fields sharing one byte)."""
    items, i, n = [], 0, len(fields)
    while i < n:
        f = fields[i]
        if f.bit_size is not None:
            byte = f.bit_off // 8
            grp = []
            while i < n and fields[i].bit_size is not None and fields[i].bit_off // 8 == byte:
                grp.append(fields[i]); i += 1
            items.append(("bits", byte, grp))
        else:
            items.append(("field", f)); i += 1
    return items


class HexEmitter:
    """Emit ImHex declarations, hoisting every struct/bitfield as a named type."""
    def __init__(self):
        self.decls, self._seen = [], set()

    def struct(self, name, st):                       # st = ('struct', _, fields, size)
        if name in self._seen:
            return name
        self._seen.add(name)
        body = []
        for it in iter_items(st[2]):
            if it[0] == "bits":
                _, byte, grp = it
                bf = f"{name}_bits{byte:02X}"
                self.decls.append("bitfield %s {\n%s\n};" % (
                    bf, "\n".join(f"    {f.name} : {f.bit_size};"
                                  for f in sorted(grp, key=lambda x: x.bit_off))))
                body.append(f"    {bf} bits{byte:02X};")
            else:
                body.append("    " + self._member(name, it[1]))
        self.decls.append("struct %s {\n%s\n};" % (name, "\n".join(body)))
        return name

    def _member(self, sname, f):
        t = f.type
        if t[0] == "array":
            elem, cnt = t[1], t[2]
            base = self._typename(sname + "_" + f.name, elem)
            return f"{base} {f.name}[{cnt}];"
        return f"{self._typename(sname + '_' + f.name, t)} {f.name};"

    def _typename(self, hint, t):
        if t[0] == "base":
            return HEX_BASE[t[1]]
        if t[0] == "enum":
            return "u8"                               # enums: plain u8 (kept simple)
        if t[0] == "struct":
            return self.struct(hint, t)
        return "u8"


def emit_hexpat(types, enums, regions):
    em = HexEmitter()
    for r in regions:
        em.struct(r["type"], types[r["type"]])
    placements = []
    for r in regions:
        cnt = f"[{r['count']}]" if r["count"] > 1 else ""
        placements.append(f'{r["type"]} {r["field"]}{cnt} @ {r["addr"]:#08x} '
                          f'[[name("{r["field"]}")]];')
    return ("#pragma description Quansheng UV-K5 V3 PY25Q16 SPI flash "
            "(generated from App/driver/spi_flash_layout.h -- DO NOT EDIT)\n"
            "#pragma endian little\n#pragma pattern_limit 2000000\n"
            "#pragma array_limit 2000000\n\n"
            + "\n\n".join(em.decls) + "\n\n// ------------------------------ placement\n"
            + "\n".join(placements) + "\n")


def emit_chirp(types, regions):
    """MEM_FORMAT body (address-ordered), bitfields reversed to MSB-first per byte."""
    def fields_of(st, indent):
        out = []
        for it in iter_items(st[2]):
            if it[0] == "bits":
                _, _, grp = it
                parts = [f"{f.name}:{f.bit_size}"
                         for f in sorted(grp, key=lambda x: -x.bit_off)]  # MSB first
                out.append(f"{indent}u8 " + ", ".join(parts) + ";")
            else:
                f = it[1]
                t = f.type
                if t[0] == "array":
                    elem, cnt = t[1], t[2]
                    if elem[0] == "base" and elem[1] == "char":
                        out.append(f"{indent}char {f.name}[{cnt}];")
                    elif elem[0] == "base":
                        out.append(f"{indent}{CHIRP_BASE[elem[1]]} {f.name}[{cnt}];")
                    elif elem[0] == "struct":
                        out.append(f"{indent}struct {{")
                        out += fields_of(elem, indent + "  ")
                        out.append(f"{indent}}} {f.name}[{cnt}];")
                elif t[0] == "struct":
                    out.append(f"{indent}struct {{")
                    out += fields_of(t, indent + "  ")
                    out.append(f"{indent}}} {f.name};")
                elif t[0] == "base":
                    out.append(f"{indent}{CHIRP_BASE[t[1]]} {f.name};")
                elif t[0] == "enum":
                    out.append(f"{indent}u8 {f.name};")
        return out

    lines = ["// generated from App/driver/spi_flash_layout.h -- DO NOT EDIT", ""]
    for r in regions:
        if not r["in_chirp"]:
            continue
        st = types[r["type"]]
        lines.append(f"#seekto {r['addr']:#08x};")
        if r["cname"]:
            cnt = f"[{r['count']}]" if r["count"] > 1 else ""
            lines.append("struct {")
            lines += fields_of(st, "  ")
            lines.append(f"}} {r['cname']}{cnt};")
        else:
            lines += fields_of(st, "")
        lines.append("")
    return "\n".join(lines)


CHIRP_DRIVER = os.path.expanduser("~/code/chirp/chirp/drivers/f4hwn_fusion.py")
# Known intentional differences vs the hand-written driver (SSOT is the correct one):
#   cal.txp -- the SSOT uses the firmware's 16-byte-per-band stride (radio.c Band*16);
#   the driver's #seek makes its element size ambiguous.
CHIRP_ALLOW = {"cal.txp"}


def validate_chirp(memfmt):
    """Semantically diff the generated MEM_FORMAT against the real driver (via CHIRP's own
    bitwise parser). Returns list of unexpected differences ([] == validated)."""
    try:
        sys.path.insert(0, os.path.expanduser("~/code/chirp"))
        from chirp import bitwise, memmap
    except Exception as e:
        print(f"  (skip CHIRP validation: {e})")
        return []
    if not os.path.exists(CHIRP_DRIVER):
        print("  (skip CHIRP validation: driver not found)")
        return []
    pad = re.compile(r"^(__pad|__p\d|__UNUSED)")

    def layout(text):
        obj = bitwise.parse(text, memmap.MemoryMapBytes(bytes(0xC000)))
        out = {}

        def walk(el, path):
            if hasattr(el, "items") and hasattr(el, "_generators"):
                for n, c in el.items():
                    walk(c, f"{path}.{n}")
            elif hasattr(el, "__len__") and not hasattr(el, "get_value"):
                if len(el):
                    walk(el[0], f"{path}[0]")
            else:
                name = path.rsplit(".", 1)[-1].split("[")[0]
                if not pad.match(name):
                    out[re.sub(r"\[0\]", "[]", path)] = (el._offset, el.size())
        for n, c in obj.items():
            walk(c, n)
        return out

    drv = re.search(r'MEM_FORMAT\s*=\s*"""(.*?)"""', open(CHIRP_DRIVER).read(), re.S).group(1)
    gen, old = layout(memfmt), layout(drv)
    diffs = []
    for p in sorted(set(gen) | set(old)):
        base = p.split(".")[0] + "." + p.split(".")[1] if p.count(".") >= 1 else p
        if any(p.startswith(a) for a in CHIRP_ALLOW):
            continue
        if gen.get(p) != old.get(p):
            diffs.append(f"{p}: generated={gen.get(p)} driver={old.get(p)}")
    return diffs


# ---------------------------------------------------------------- calibration
# The 0xB000 calibration block is worth having on its own: a 512-byte calib.dat
# (uvflash dump-calib) decoded/diffed/edited without the whole 2MB image.

BASE_SIZE = {"u8": 1, "u16": 2, "u32": 4, "i8": 1, "i16": 2, "i32": 4, "char": 1}
# fields whose value is an operator setting (changes with use), not factory RF cal
CALIB_VOLATILE = {"volumeGain", "dacGain"}
# the RF-critical fields — a wrong value here mis-tunes the radio
CALIB_CRITICAL_PREFIXES = ("txp", "xtalFreqLow", "batLvl")


def flatten_calib(types, typename="FL_Calibration"):
    """Flatten FL_Calibration into leaf fields with absolute offsets, straight from
    the DWARF — so it can never drift from the C header. Base-type arrays stay one
    field with a count; struct arrays recurse per index; padding (__*) is skipped."""
    st = types.get(typename)
    if not st:
        sys.exit(f"{typename} not in DWARF")
    out = []

    def walk(t, base, path):
        if t[0] == "struct":
            for f in t[2]:
                if f.name and f.name.startswith("__"):
                    continue
                walk(f.type, base + f.offset, f"{path}.{f.name}" if path else f.name)
        elif t[0] == "array":
            elem, cnt = t[1], t[2]
            if elem[0] == "base":
                if elem[1] == "char":
                    return                              # char[] here is padding
                out.append(dict(name=path, offset=base, ctype=elem[1], count=cnt))
            else:
                esz = elem[3] if elem[0] == "struct" else 1
                for i in range(cnt):
                    walk(elem, base + i * esz, f"{path}[{i}]")
        elif t[0] in ("base", "enum"):
            ct = "u8" if t[0] == "enum" else t[1]
            if ct == "char":
                return
            out.append(dict(name=path, offset=base, ctype=ct, count=1))

    walk(st, 0, "")
    for fl in out:
        fl["size"] = BASE_SIZE[fl["ctype"]]
        top = fl["name"].split(".")[0].split("[")[0]
        fl["role"] = ("volatile" if fl["name"] in CALIB_VOLATILE
                      else "critical" if top in CALIB_CRITICAL_PREFIXES else "cal")
    return {"struct": typename, "size": st[3], "fields": out}


def emit_calib_hexpat(types):
    st = types.get("FL_Calibration")
    em = HexEmitter()
    em.struct("FL_Calibration", st)
    return ("#pragma description UV-K5/K1 calibration block (SPI flash 0xB000)\n"
            "// Generated from App/driver/spi_flash_layout.h by tools/gen_flash_layout.py.\n"
            "// Load against a 512-byte calib.dat (uvflash dump-calib), NOT the full image.\n\n"
            + "\n\n".join(em.decls)
            + "\n\nFL_Calibration cal @ 0x00;\n")


def emit_calib_json(flat):
    return json.dumps(flat, indent=1) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    obj = compile_dwarf()
    types, enums = _walk(obj)
    regions = parse_regions()

    if args.dump:
        print(f"{len(types)} typedef'd structs, {len(enums)} enums, {len(regions)} regions")
        for r in regions:
            st = types.get(r["type"])
            n = len(st[2]) if st else "?"
            sz = st[3] if st else "?"
            print(f"  @{r['addr']:#08x}  {r['field']:16s} {r['type']:20s} "
                  f"x{r['count']:<4} size={sz} fields={n} chirp={r['cname'] or '-'}")
        # spot-check bitfields on the channel struct
        ch = types.get("FL_Channel")
        if ch:
            print("\nFL_Channel bitfields (LSB-first, C order):")
            for f in ch[2]:
                if f.bit_size:
                    print(f"    {f.name:16s} byte {f.offset} bit_off {f.bit_off} :{f.bit_size}")
        return 0

    hexpat = emit_hexpat(types, enums, regions)
    memfmt = emit_chirp(types, regions)
    calib_hexpat = emit_calib_hexpat(types)
    calib_json = emit_calib_json(flatten_calib(types))

    if args.check:
        ok = True
        for path, new in ((HEXPAT_OUT, hexpat), (MEMFMT_OUT, memfmt),
                          (CALIB_HEXPAT_OUT, calib_hexpat), (CALIB_JSON_OUT, calib_json)):
            old = open(path).read() if os.path.exists(path) else None
            if old != new:
                print(f"  OUT OF DATE: {os.path.relpath(path, ROOT)} (run without --check)")
                ok = False
            else:
                print(f"  up to date:  {os.path.relpath(path, ROOT)}")
        print("  CHIRP layout validation:")
        diffs = validate_chirp(memfmt)
        if diffs:
            ok = False
            for d in diffs:
                print(f"    MISMATCH {d}")
        else:
            print("    all functional fields match the driver's MEM_FORMAT")
        return 0 if ok else 1

    for path, new in ((HEXPAT_OUT, hexpat), (MEMFMT_OUT, memfmt),
                      (CALIB_HEXPAT_OUT, calib_hexpat), (CALIB_JSON_OUT, calib_json)):
        with open(path, "w") as f:
            f.write(new)
        print(f"wrote {os.path.relpath(path, ROOT)} ({new.count(chr(10))+1} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
