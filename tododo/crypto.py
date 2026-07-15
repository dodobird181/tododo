"""
Per-file AES-256-GCM encryption for the event mirror.

Each ciphertext file is:

    MAGIC(4) | version(1) | salt(16) | nonce(12) | ciphertext+tag

The 32-byte key is derived from a passphrase with PBKDF2-HMAC-SHA256 and a fresh
random salt per file, so identical plaintext yields different ciphertext. GCM's
tag authenticates the contents: a wrong passphrase or any tampering fails to
decrypt. Events are immutable, so a file is encrypted once and never re-encrypted.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"TDDO"
VERSION = 1
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
DEFAULT_ITERATIONS = 200000

_HEADER_LEN = len(MAGIC) + 1 + SALT_LEN + NONCE_LEN


class CryptoError(Exception):
    pass


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(plaintext: bytes, passphrase: str, iterations: int = DEFAULT_ITERATIONS) -> bytes:
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = derive_key(passphrase, salt, iterations)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return MAGIC + bytes([VERSION]) + salt + nonce + ciphertext


def decrypt(blob: bytes, passphrase: str, iterations: int = DEFAULT_ITERATIONS) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < _HEADER_LEN:
        raise CryptoError("not a tododo ciphertext blob")
    offset = len(MAGIC) + 1
    salt = blob[offset:offset + SALT_LEN]
    nonce = blob[offset + SALT_LEN:offset + SALT_LEN + NONCE_LEN]
    ciphertext = blob[_HEADER_LEN:]
    key = derive_key(passphrase, salt, iterations)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception:
        raise CryptoError("decryption failed (wrong passphrase or tampered file)")
