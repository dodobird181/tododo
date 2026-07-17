"""
Envelope AES-256-GCM encryption for the event mirror.

Two on-disk ciphertext formats exist:

    v1 (legacy):  MAGIC(4) | version(1) | salt(16) | nonce(12) | ciphertext+tag
    v2 (current): MAGIC(4) | version(1) |           nonce(12) | ciphertext+tag

v1 derived the 32-byte AES key from the passphrase and a fresh random salt *per
file*, so decrypting a whole log paid one PBKDF2-HMAC-SHA256 pass (200k
iterations, ~10ms) for every event — tens of seconds on a fresh clone.

v2 uses envelope encryption. A random 32-byte *data key* (DEK) encrypts every
event; the DEK is itself encrypted ("wrapped") under a *key-encrypting key* (KEK)
that is derived from the passphrase, and the wrapped DEK is stored in a small
`keyring` file beside the encrypted events. Consequences:

  - Bulk decryption pays one PBKDF2 pass total (to unwrap the DEK once), not one
    per file, and stays flat as the log grows.
  - Changing the passphrase only re-wraps the DEK — a single tiny file rewrite —
    instead of re-encrypting every event. See `rewrap_keyring`.
  - Rotating the data key itself (a real compromise) still re-encrypts every
    event, which is unavoidable.

Each file gets a fresh random 12-byte nonce, so identical plaintext yields
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

import yaml
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
KEYRING_FILENAME = "keyring"
KEYRING_VERSION = 1

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
    True if the blob uses the current envelope (v2) format.
    """
    return blob_version(blob) == VERSION_SHARED_KEY


# --- keyring: the wrapped data key -------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _wrap_data_key(data_key: bytes, passphrase: str, salt: bytes, iterations: int) -> bytes:
    kek = derive_key(passphrase, salt, iterations)
    nonce = os.urandom(NONCE_LEN)
    return nonce + AESGCM(kek).encrypt(nonce, data_key, None)


def _unwrap_data_key(wrapped: bytes, passphrase: str, salt: bytes, iterations: int) -> bytes:
    kek = derive_key(passphrase, salt, iterations)
    nonce, ciphertext = wrapped[:NONCE_LEN], wrapped[NONCE_LEN:]
    try:
        return AESGCM(kek).decrypt(nonce, ciphertext, None)
    except Exception:
        raise CryptoError("wrong passphrase for workspace keyring")


def keyring_path(directory: Path) -> Path:
    return Path(directory) / KEYRING_FILENAME


def write_keyring(directory: Path, passphrase: str, data_key: bytes,
                  iterations: int = DEFAULT_ITERATIONS) -> None:
    """
    Wrap `data_key` under a KEK derived from `passphrase` and persist it. The
    keyring is not secret on its own — the wrapped key is useless without the
    passphrase — so it syncs through git like the encrypted events.
    """
    salt = os.urandom(SALT_LEN)
    wrapped = _wrap_data_key(data_key, passphrase, salt, iterations)
    document = {
        "version": KEYRING_VERSION,
        "iterations": iterations,
        "kek_salt": salt.hex(),
        "wrapped_data_key": wrapped.hex(),
    }
    _atomic_write_text(keyring_path(directory), yaml.safe_dump(document, sort_keys=False))


def load_data_key(directory: Path, passphrase: str) -> bytes:
    """
    Read the keyring and return the unwrapped data key. Raises `CryptoError`
    with a clear message if the passphrase is wrong.
    """
    path = keyring_path(directory)
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    salt = bytes.fromhex(document["kek_salt"])
    iterations = int(document["iterations"])
    wrapped = bytes.fromhex(document["wrapped_data_key"])
    return _unwrap_data_key(wrapped, passphrase, salt, iterations)


def load_or_create_data_key(directory: Path, passphrase: str,
                            iterations: int = DEFAULT_ITERATIONS) -> bytes:
    """
    Return the workspace data key, creating a fresh random one (wrapped under
    `passphrase`) on first use.
    """
    if keyring_path(directory).exists():
        return load_data_key(directory, passphrase)
    data_key = os.urandom(KEY_LEN)
    write_keyring(directory, passphrase, data_key, iterations)
    return data_key


def rewrap_keyring(directory: Path, old_passphrase: str, new_passphrase: str,
                   iterations: int = DEFAULT_ITERATIONS) -> None:
    """
    Change the passphrase without touching any event: unwrap the data key with
    the old passphrase and re-wrap it with the new one. This is the cheap
    passphrase rotation.
    """
    data_key = load_data_key(directory, old_passphrase)
    write_keyring(directory, new_passphrase, data_key, iterations)


# --- the cipher ---------------------------------------------------------------


class Cipher:
    """
    Encrypt/decrypt event bytes with the workspace data key (DEK).

    `legacy_passphrase` is retained only so pre-envelope v1 blobs (which carry
    their own salt) can still be decrypted with a per-file key derivation, e.g.
    while migrating an old log to the envelope format.
    """

    def __init__(self, data_key: bytes, legacy_passphrase: str = "",
                 legacy_iterations: int = DEFAULT_ITERATIONS):
        self._aes = AESGCM(data_key)
        self._legacy_passphrase = legacy_passphrase
        self._legacy_iterations = legacy_iterations

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
            raise CryptoError("decryption failed (wrong key or tampered file)")

    def _decrypt_legacy(self, blob: bytes) -> bytes:
        if len(blob) < _LEGACY_HEADER_LEN:
            raise CryptoError("truncated legacy ciphertext blob")
        salt = blob[_LEGACY_SALT_OFFSET:_LEGACY_NONCE_OFFSET]
        nonce = blob[_LEGACY_NONCE_OFFSET:_LEGACY_HEADER_LEN]
        ciphertext = blob[_LEGACY_HEADER_LEN:]
        key = derive_key(self._legacy_passphrase, salt, self._legacy_iterations)
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception:
            raise CryptoError("decryption failed (wrong passphrase or tampered file)")
