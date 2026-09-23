# -*- coding: utf-8 -*-
"""Force zlib-CRC32 of a buffer to a target value by rewriting 4 bytes at a given offset.
(Nayuki forcecrc32 method.) Used because the release's YooAsset passes the manifest CRC to
AssetBundle.LoadFromStream, and Unity checks CRC32 of the uncompressed SerializedFile content."""
import zlib

POLY = 0x104C11DB7

def _rev32(x):
    return int("{:032b}".format(x)[::-1], 2)

def _mul(x, y):
    z = 0
    while y:
        z ^= x * (y & 1)
        y >>= 1
        x <<= 1
        if (x >> 32) & 1:
            x ^= POLY
    return z

def _pow(x, y):
    z = 1
    while y:
        if y & 1:
            z = _mul(z, x)
        x = _mul(x, x)
        y >>= 1
    return z

def _divmod(x, y):
    if x == 0:
        return 0, 0
    yd = y.bit_length() - 1
    z = 0
    for i in range(x.bit_length() - 1 - yd, -1, -1):
        if (x >> (i + yd)) & 1:
            x ^= y << i
            z |= 1 << i
    return z, x

def _recip(x):
    y, x = x, POLY
    a, b = 0, 1
    while y:
        q, r = _divmod(x, y)
        a, b = b, a ^ _mul(q, b)
        x, y = y, r
    if x != 1:
        raise ValueError("not invertible")
    return a

def force_crc32(buf: bytearray, offset: int, target: int) -> bytearray:
    target = _rev32(target & 0xFFFFFFFF)
    delta = _rev32(zlib.crc32(buf) & 0xFFFFFFFF) ^ target
    delta = _mul(_recip(_pow(2, (len(buf) - offset) * 8)), delta)
    d = _rev32(delta)
    for i in range(4):
        buf[offset + i] ^= (d >> (i * 8)) & 0xFF
    return buf
