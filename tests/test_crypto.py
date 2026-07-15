"""
Encrypt/decrypt round-trip, wrong-passphrase rejection, tamper detection, and
per-call salt/nonce uniqueness.
"""

from __future__ import annotations

import pytest

from tododo import crypto


def test_round_trip():
    blob = crypto.encrypt(b"hello world", "passphrase")
    assert crypto.decrypt(blob, "passphrase") == b"hello world"


def test_wrong_passphrase_fails():
    blob = crypto.encrypt(b"secret", "right")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(blob, "wrong")


def test_tamper_detected():
    blob = bytearray(crypto.encrypt(b"secret", "pw"))
    blob[-1] ^= 0x01
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(bytes(blob), "pw")


def test_unique_salt_and_nonce_per_call():
    a = crypto.encrypt(b"same", "pw")
    b = crypto.encrypt(b"same", "pw")
    assert a != b
    assert crypto.decrypt(a, "pw") == crypto.decrypt(b, "pw") == b"same"


def test_non_ciphertext_rejected():
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(b"not encrypted", "pw")
