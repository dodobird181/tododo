"""Optional AES-256-GCM encryption of data files at rest.

When enabled (``encryption: true`` in settings), the store encrypts each item /
board file's bytes on write and decrypts on read. The key is a 32-byte AES-256
key sourced from the ``TODODO_KEY`` environment variable (base64 or hex), or a
key-file path configured in settings.

On-disk format for an encrypted file::

    TODODO-ENC-1\n
    <base64( nonce(12) || ciphertext_with_gcm_tag )>

The magic header lets the store detect ciphertext (so an accidental
plaintext/ciphertext mix, or toggling the setting, degrades gracefully).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"TODODO-ENC-1\n"
_NONCE = 12


class CryptoError(Exception):
    pass


def _decode_key(raw: str) -> bytes:
    raw = raw.strip()
    # Try base64 first, then hex; require exactly 32 bytes for AES-256.
    for decoder in (base64.b64decode, bytes.fromhex):
        try:
            key = decoder(raw)
        except Exception:
            continue
        if len(key) == 32:
            return key
    raise CryptoError("TODODO key must decode (base64 or hex) to exactly 32 bytes")


def load_key(key_file: str | Path | None = None) -> bytes | None:
    """Resolve the AES key from env or a key file. Returns None when unavailable."""
    env = os.environ.get("TODODO_KEY")
    if env:
        return _decode_key(env)
    if key_file:
        p = Path(key_file)
        if p.exists():
            return _decode_key(p.read_text(encoding="utf-8"))
    return None


class Cipher:
    """Encrypt/decrypt file bytes with AES-256-GCM. A missing key disables it."""

    def __init__(self, key: bytes | None):
        self._aes = AESGCM(key) if key else None

    @property
    def enabled(self) -> bool:
        return self._aes is not None

    def is_encrypted(self, data: bytes) -> bool:
        return data.startswith(MAGIC)

    def encrypt(self, plaintext: bytes) -> bytes:
        if not self._aes:
            return plaintext
        nonce = os.urandom(_NONCE)
        blob = nonce + self._aes.encrypt(nonce, plaintext, None)
        return MAGIC + base64.b64encode(blob)

    def decrypt(self, data: bytes) -> bytes:
        if not data.startswith(MAGIC):
            return data  # plaintext (encryption off when written, or mixed state)
        if not self._aes:
            raise CryptoError("file is encrypted but no key is configured")
        blob = base64.b64decode(data[len(MAGIC):])
        nonce, ct = blob[:_NONCE], blob[_NONCE:]
        return self._aes.decrypt(nonce, ct, None)
