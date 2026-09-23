# -*- coding: utf-8 -*-
"""Parse The Piper Of Dawn Language / LanguageTalk decrypted protobuf tables.
Top-level: [4-byte LE header] repeated f1 int64 keys + repeated f2 row-messages.
Row (SilentOrbit): f1=int id, f2=str, f3=int, f4=str, f5=str, f6=str.
"""
import io

def _read_varint(buf, i):
    shift = 0; result = 0
    while True:
        b = buf[i]; i += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80): break
        shift += 7
    return result, i

def parse_row(data):
    i = 0; row = {}
    n = len(data)
    while i < n:
        tag, i = _read_varint(data, i)
        field = tag >> 3; wt = tag & 7
        if wt == 0:      # varint
            v, i = _read_varint(data, i); row[field] = v
        elif wt == 2:    # length-delim (string)
            ln, i = _read_varint(data, i)
            row[field] = data[i:i+ln].decode("utf-8", "surrogateescape"); i += ln
        else:
            raise ValueError(f"unexpected wiretype {wt} field {field}")
    return row

def parse_table(pt, lenient=True):
    # detect 4-byte header: try offset 4 first (header present), fallback 0
    best = None
    for start in (4, 0):
        try:
            i = start; keys = []; rows = []
            n = len(pt); trailing = 0
            while i < n:
                tag, i = _read_varint(pt, i)
                field = tag >> 3; wt = tag & 7
                if field == 1 and wt == 0:
                    v, i = _read_varint(pt, i); keys.append(v)
                elif field == 2 and wt == 2:
                    ln, i = _read_varint(pt, i)
                    rows.append(parse_row(pt[i:i+ln])); i += ln
                else:
                    if lenient and rows:
                        trailing = n - i
                        break            # stop at unparseable trailing bytes
                    raise ValueError(f"bad top tag field={field} wt={wt} at {i}")
            if best is None or len(rows) > len(best[2]):
                best = (start, keys, rows, trailing)
            if rows:
                return best[0], best[1], best[2]
        except Exception as e:
            last = e
            continue
    if best:
        return best[0], best[1], best[2]
    raise last

if __name__ == "__main__":
    import sys, piper_crypto as pc
    pt = pc.decrypt(open(sys.argv[1], "rb").read())
    start, keys, rows = parse_table(pt)
    print(f"header_off={start} keys={len(keys)} rows={len(rows)}")
    for r in rows[:4]:
        print("  ", r)
