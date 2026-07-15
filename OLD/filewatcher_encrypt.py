#!/usr/bin/env python3
"""Watch a folder and AES-256 encrypt changed files into a sister folder.

Watches WATCH_FOLDER (recursively). On any create/modify/move, the affected
file is AES-256-GCM encrypted and written to a mirror tree under
"<WATCH_FOLDER>-encrypted", preserving relative paths (with a ".enc" suffix).
Deletes are mirrored too (the corresponding .enc file is removed).

Encryption
----------
AES-256-GCM (authenticated encryption). The 32-byte key is derived from the
configured passphrase with PBKDF2-HMAC-SHA256 using a random per-file salt.
Each output file is:

    MAGIC(4) | version(1) | salt(16) | nonce(12) | ciphertext+tag

Config (CLI flags override env vars)
------------------------------------
    --folder / FWENC_FOLDER      folder to watch
    --key    / FWENC_KEY         passphrase used to derive the AES key
    --iters  / FWENC_ITERS       PBKDF2 iterations (default 200000)

Usage
-----
    pip install watchdog cryptography
    python filewatcher_encrypt.py --folder ./secret --key 'my passphrase'
    # or:
    FWENC_FOLDER=./secret FWENC_KEY='my passphrase' python filewatcher_encrypt.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

MAGIC = b"FWE1"
VERSION = 1
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32  # AES-256


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_bytes(plaintext: bytes, passphrase: str, iterations: int) -> bytes:
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = derive_key(passphrase, salt, iterations)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return MAGIC + bytes([VERSION]) + salt + nonce + ct


class EncryptHandler(FileSystemEventHandler):
    def __init__(self, src_root: Path, dst_root: Path, passphrase: str, iterations: int):
        self.src_root = src_root
        self.dst_root = dst_root
        self.passphrase = passphrase
        self.iterations = iterations

    def _dst_for(self, src: Path) -> Path:
        rel = src.relative_to(self.src_root)
        return self.dst_root / rel.with_name(rel.name + ".enc")

    def encrypt_file(self, src: Path) -> None:
        try:
            data = src.read_bytes()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return  # vanished or not readable between event and read; skip
        blob = encrypt_bytes(data, self.passphrase, self.iterations)
        dst = self._dst_for(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, dst)  # atomic swap
        print(f"encrypted {src.relative_to(self.src_root)} -> {dst.relative_to(self.dst_root)}")

    def remove_file(self, src: Path) -> None:
        dst = self._dst_for(src)
        try:
            dst.unlink()
            print(f"removed   {dst.relative_to(self.dst_root)}")
        except FileNotFoundError:
            pass

    # watchdog callbacks -------------------------------------------------
    def on_created(self, event):
        if not event.is_directory:
            self.encrypt_file(Path(event.src_path))

    def on_modified(self, event):
        if not event.is_directory:
            self.encrypt_file(Path(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        self.remove_file(Path(event.src_path))
        self.encrypt_file(Path(event.dest_path))

    def on_deleted(self, event):
        if not event.is_directory:
            self.remove_file(Path(event.src_path))


def initial_sync(handler: EncryptHandler) -> None:
    """Encrypt everything already present at startup."""
    for path in handler.src_root.rglob("*"):
        if path.is_file():
            handler.encrypt_file(path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch a folder and AES-256 encrypt changes.")
    p.add_argument("--folder", default=os.environ.get("FWENC_FOLDER"),
                   help="folder to watch (env: FWENC_FOLDER)")
    p.add_argument("--key", default=os.environ.get("FWENC_KEY"),
                   help="passphrase for AES key derivation (env: FWENC_KEY)")
    p.add_argument("--iters", type=int,
                   default=int(os.environ.get("FWENC_ITERS", "200000")),
                   help="PBKDF2 iterations (env: FWENC_ITERS, default 200000)")
    p.add_argument("--no-initial-sync", action="store_true",
                   help="skip encrypting existing files at startup")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.folder:
        sys.exit("error: --folder (or FWENC_FOLDER) required")
    if not args.key:
        sys.exit("error: --key (or FWENC_KEY) required")

    src_root = Path(args.folder).expanduser().resolve()
    if not src_root.is_dir():
        sys.exit(f"error: not a directory: {src_root}")

    dst_root = src_root.parent / f"{src_root.name}-encrypted"
    dst_root.mkdir(parents=True, exist_ok=True)

    handler = EncryptHandler(src_root, dst_root, args.key, args.iters)

    if not args.no_initial_sync:
        initial_sync(handler)

    observer = Observer()
    observer.schedule(handler, str(src_root), recursive=True)
    observer.start()
    print(f"watching {src_root}\n  -> {dst_root}\n(Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
