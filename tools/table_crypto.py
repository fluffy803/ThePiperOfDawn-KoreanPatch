# -*- coding: utf-8 -*-
"""The Piper Of Dawn - GameRes/Table decrypt & encrypt.
Scheme (from Assembly-CSharp bz.ekp + BouncyCastle RSAHelper):
  stored = [128B RSA-PKCS1(type1) over first 100 plaintext bytes] + plaintext[100:]
  then ekp applies two sparse XOR passes on the assembled array 'a' (len = stored_len - 28).
Decrypt: RSA-decrypt-with-pubkey(first128)->100B ; a = 100B + stored[128:] ; apply XOR (involutive).
Encrypt (header unchanged): a = plaintext ; apply XOR ; stored = orig_first128 + a[100:].
"""
from Crypto.PublicKey import RSA

PUBKEY_B64 = ("MIGdMA0GCSqGSIb3DQEBAQUAA4GLADCBhwKBgQCyJiTzABL2wironv9+4wnZTg7JXr1e"
              "kiMA3RdL2e+W8kEtyZgghb5KBBAASKuiGNxhadrnSgC8+h1r7B/JLudatvdlzwyy1gA"
              "s/mbVYHd7x1WoBfzDpWkZX8bhDO/uX4GnBhWAmtapbbjVGOAVIuaIV8lBzNXJ30mJPD"
              "I4wKc7/QIBAw==")
_key = RSA.import_key(__import__("base64").b64decode(PUBKEY_B64))
N, E = _key.n, _key.e  # E == 3

def _rsa_pub_decrypt(block128: bytes) -> bytes:
    c = int.from_bytes(block128, "big")
    m = pow(c, E, N)
    em = m.to_bytes(128, "big")
    # PKCS1 type 1: 0x00 0x01 0xFF..0xFF 0x00 <data>
    assert em[0] == 0x00 and em[1] == 0x01, f"bad PKCS1 header {em[:2].hex()}"
    idx = em.find(b"\x00", 2)
    assert idx != -1, "no 0x00 separator"
    return em[idx+1:]  # data (100 bytes)

def _xor_passes(a: bytearray) -> None:
    n = len(a)
    num, num2 = 1, 0
    while num < n:
        a[num] ^= (num - num2) & 0xFF
        num <<= 1; num2 += 1
    num3, num4 = n - 1, 0
    while num3 > 0:
        a[num3] ^= (num3 - num4) & 0xFF
        num3 >>= 1; num4 += 1

def decrypt(stored: bytes) -> bytes:
    data100 = _rsa_pub_decrypt(stored[:128])
    a = bytearray(data100 + stored[128:])
    _xor_passes(a)              # involutive: undoes the game's obfuscation
    return bytes(a)

def encrypt(plaintext: bytes, orig_first128: bytes) -> bytes:
    """Re-pack, keeping the RSA header block unchanged (first 100 plaintext bytes must match)."""
    a = bytearray(plaintext)
    _xor_passes(a)              # re-apply (involutive)
    return orig_first128 + bytes(a[100:])

if __name__ == "__main__":
    import sys
    stored = open(sys.argv[1], "rb").read()
    pt = decrypt(stored)
    sys.stdout.buffer.write(pt)
