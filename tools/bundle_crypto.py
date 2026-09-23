# -*- coding: utf-8 -*-
"""YooAsset bundle encryption used by The Piper Of Dawn (Steam release).

master = SHA256( concat_i( A[p[i]] XOR B[p[i]] ) )   (8-byte constant pairs, p = [2,0,3,1])
key    = HMAC-SHA256(master, UTF8("bundle-key|" + bundleName.strip().lower()))   -> AES-256
ctr0   = HMAC-SHA256(master, UTF8("bundle-iv|"  + bundleName.strip().lower()))[:16]
keystream block n = AES-256-ECB(key, ctr0[:8] + BE64(n))   (symmetric)
"""
import hashlib
import hmac
import struct

from Crypto.Cipher import AES

_L = lambda v: struct.pack("<q", v)
_A = [_L(-2778017481869778894), _L(5453220943767804905), _L(-1212590057535674680), _L(7756687243728731016)]
_B = [_L(2468874309672470050), _L(3778238437607726704), _L(-8367760454150882655), _L(-2915879987029010993)]
_PERM = [2, 0, 3, 1]

def _master():
    buf = bytearray(32)
    for i, idx in enumerate(_PERM):
        for j in range(8):
            buf[i * 8 + j] = _A[idx][j] ^ _B[idx][j]
    return hashlib.sha256(bytes(buf)).digest()

MASTER = _master()

def _derive(prefix, name):
    return hmac.new(MASTER, (prefix + name.strip().lower()).encode("utf-8"), hashlib.sha256).digest()

def crypt(data, bundle_name):
    key = _derive("bundle-key|", bundle_name)
    ctr0 = _derive("bundle-iv|", bundle_name)[:16]
    return AES.new(key, AES.MODE_CTR, nonce=ctr0[:8], initial_value=0).encrypt(data)
