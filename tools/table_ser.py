# -*- coding: utf-8 -*-
"""Serialize Piper tables back to plaintext protobuf (SilentOrbit layout).
Top-level: [uint32 LE rowcount] + repeated f1 varint keys + repeated f2 len-delim rows.
Row: fields in ascending number; int->varint, str->len-delim utf8."""
import struct

def enc_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        if n: out.append(b | 0x80)
        else: out.append(b); break
    return bytes(out)

def serialize_row(row):
    out = bytearray()
    for f in sorted(row):
        v = row[f]
        if isinstance(v, int):
            out += enc_varint((f << 3) | 0) + enc_varint(v)
        else:
            b = v.encode("utf-8", "surrogateescape")
            out += enc_varint((f << 3) | 2) + enc_varint(len(b)) + b
    return bytes(out)

def serialize_table(keys, rows):
    out = bytearray(struct.pack("<I", len(rows)))
    for k in keys:
        out += enc_varint((1 << 3) | 0) + enc_varint(k)
    for r in rows:
        rb = serialize_row(r)
        out += enc_varint((2 << 3) | 2) + enc_varint(len(rb)) + rb
    return bytes(out)

if __name__ == "__main__":
    import sys, piper_crypto as pc, table_proto as tp
    pt = pc.decrypt(open(sys.argv[1], "rb").read())
    _, keys, rows = tp.parse_table(pt, lenient=False)
    re = serialize_table(keys, rows)
    print(f"orig_len={len(pt)} reser_len={len(re)} byte_exact={re==pt}")
    if re != pt:
        i = 0
        while i < min(len(re), len(pt)) and re[i] == pt[i]: i += 1
        print(f"  first diff at 0x{i:x}: orig={pt[i:i+12].hex()} reser={re[i:i+12].hex()}")
