"""
AES-256-GCM encryption for the event mirror with a workspace-shared key.

Two on-disk ciphertext formats exist:

    v1 (legacy):  MAGIC(4) | version(1) | salt(16) | nonce(12) | ciphertext+tag
    v2 (current): MAGIC(4) | version(1) |           nonce(12) | ciphertext+tag

v1 derived the 32-byte AES key from the passphrase and a fresh random salt *per
file*, so decrypting a whole log paid one PBKDF2-HMAC-SHA256 pass (200k
iterations, ~10ms) for every event — tens of seconds on a fresh clone.

v2 derives the key exactly *once* from the passphrase and a single
workspace-level salt (see `load_or_create_salt`), then reuses it for every file.
Bulk decryption drops to one PBKDF2 pass total and stays flat as the log grows.
Each file still gets a fresh random 12-byte nonce, so identical plaintext yields
different ciphertext and GCM's uniqueness requirement holds (random 96-bit
nonces are safe well past millions of files under one key).

GCM's tag authenticates the contents: a wrong passphrase or any tampering fails
to decrypt. Events are immutable, so a file is encrypted once. `Cipher.decrypt`
understands both formats, so an existing v1 log stays readable and can be
migrated in place.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"TDDO"
VERSION_PER_FILE_SALT = 1
VERSION_SHARED_KEY = 2
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
DEFAULT_ITERATIONS = 200000
SALT_FILENAME = "keysalt"

_VERSION_OFFSET = len(MAGIC)
_LEGACY_SALT_OFFSET = _VERSION_OFFSET + 1
_LEGACY_NONCE_OFFSET = _LEGACY_SALT_OFFSET + SALT_LEN
_LEGACY_HEADER_LEN = _LEGACY_NONCE_OFFSET + NONCE_LEN
_SHARED_NONCE_OFFSET = _VERSION_OFFSET + 1
_SHARED_HEADER_LEN = _SHARED_NONCE_OFFSET + NONCE_LEN


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


def blob_version(blob: bytes) -> int:
    """
    Return the format version byte of a ciphertext blob.
    """
    if not blob.startswith(MAGIC) or len(blob) < _VERSION_OFFSET + 1:
        raise CryptoError("not a tododo ciphertext blob")
    return blob[_VERSION_OFFSET]


def is_shared_key(blob: bytes) -> bool:
    """
    True if the blob uses the current shared-key (v2) format.
    """
    return blob_version(blob) == VERSION_SHARED_KEY


def load_or_create_salt(directory: Path) -> bytes:
    """
    Return the workspace key-derivation salt from `directory`, creating it on
    first use. The salt is not secret; it lives beside the encrypted events and
    syncs through git so every clone derives the same key. Once written it must
    never change, or previously-written v2 files become undecryptable.
    """
    directory = Path(directory)
    path = directory / SALT_FILENAME
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())
    salt = os.urandom(SALT_LEN)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(salt.hex(), encoding="utf-8")
    os.replace(temporary, path)
    return salt


class Cipher:
    """
    Encrypt/decrypt event bytes with a workspace-shared AES-256-GCM key.

    The key is derived once, at construction, from the passphrase and salt. The
    passphrase is also retained so legacy v1 blobs (which carry their own salt)
    can still be decrypted with a per-file key derivation.
    """

    def __init__(self, passphrase: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS):
        self._passphrase = passphrase
        self._iterations = iterations
        self._key = derive_key(passphrase, salt, iterations)
        self._aes = AESGCM(self._key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(NONCE_LEN)
        ciphertext = self._aes.encrypt(nonce, plaintext, None)
        return MAGIC + bytes([VERSION_SHARED_KEY]) + nonce + ciphertext

    def decrypt(self, blob: bytes) -> bytes:
        version = blob_version(blob)
        if version == VERSION_SHARED_KEY:
            return self._decrypt_shared(blob)
        if version == VERSION_PER_FILE_SALT:
            return self._decrypt_legacy(blob)
        raise CryptoError(f"unknown ciphertext version {version}")

    def _decrypt_shared(self, blob: bytes) -> bytes:
        if len(blob) < _SHARED_HEADER_LEN:
            raise CryptoError("truncated shared-key ciphertext blob")
        nonce = blob[_SHARED_NONCE_OFFSET:_SHARED_HEADER_LEN]
        ciphertext = blob[_SHARED_HEADER_LEN:]
        try:
            return self._aes.decrypt(nonce, ciphertext, None)
        except Exception:
            raise CryptoError("decryption failed (wrong passphrase or tampered file)")

    def _decrypt_legacy(self, blob: bytes) -> bytes:
        if len(blob) < _LEGACY_HEADER_LEN:
            raise CryptoError("truncated legacy ciphertext blob")
        salt = blob[_LEGACY_SALT_OFFSET:_LEGACY_NONCE_OFFSET]
        nonce = blob[_LEGACY_NONCE_OFFSET:_LEGACY_HEADER_LEN]
        ciphertext = blob[_LEGACY_HEADER_LEN:]
        key = derive_key(self._passphrase, salt, self._iterations)
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception:
            raise CryptoError("decryption failed (wrong passphrase or tampered file)")
