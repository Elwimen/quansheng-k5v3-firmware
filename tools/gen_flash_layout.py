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
                fields.append(Field(
                    nm(m), resolve(die_at(m.attributes["DW_AT_type"].value, cu_base), cu_base),
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

    print(f"model: {len(types)} structs, {len(enums)} enums, {len(regions)} regions")
    print("(hexpat + MEM_FORMAT emitters: next step)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
