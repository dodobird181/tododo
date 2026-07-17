"""
Key maintenance for the encrypted event log.

    python -m tododo.rekey migrate [--old-passphrase P] [--root DIR]
    python -m tododo.rekey rewrap  [--old-passphrase P] [--root DIR]

`migrate` re-encrypts a legacy (v1, per-file-salt) log into the envelope (v2)
format under a fresh random data key wrapped by TODODO_PASSPHRASE. It needs the
OLD passphrase the v1 files were encrypted with. Run once, then commit the
result. This is the one expensive rotation.

`rewrap` changes the passphrase without touching any event: it unwraps the data
key with the old passphrase and re-wraps it under TODODO_PASSPHRASE, rewriting
only the small `keyring` file. This is the cheap passphrase rotation.

In both cases TODODO_PASSPHRASE is the new/current passphrase. The old
passphrase is read interactively (no shell-history leak) unless --old-passphrase
is given.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from tododo.crypto import Cipher
from tododo.crypto import CryptoError
from tododo.crypto import DEFAULT_ITERATIONS
from tododo.crypto import is_shared_key
from tododo.crypto import keyring_path
from tododo.crypto import load_or_create_data_key
from tododo.crypto import rewrap_keyring

ENC_SUFFIX = ".yaml.enc"


def _new_passphrase() -> str:
    passphrase = os.environ.get("TODODO_PASSPHRASE", "").strip()
    if not passphrase:
        sys.exit("error: TODODO_PASSPHRASE is not set (it is the new/current passphrase)")
    return passphrase


def _old_passphrase(explicit: str | None, prompt: str) -> str:
    passphrase = (explicit or getpass.getpass(prompt)).strip()
    if not passphrase:
        sys.exit("error: old passphrase is empty")
    return passphrase


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def migrate(root: Path, explicit_old: str | None, iterations: int) -> int:
    new_passphrase = _new_passphrase()
    old_passphrase = _old_passphrase(explicit_old, "Old (legacy) passphrase: ")
    encrypted_dir = root / "events-encrypted"
    events_dir = root / "events"
    if not encrypted_dir.is_dir():
        sys.exit(f"error: no encrypted log at {encrypted_dir}")
    events_dir.mkdir(parents=True, exist_ok=True)

    data_key = load_or_create_data_key(encrypted_dir, new_passphrase, iterations)
    cipher = Cipher(data_key, legacy_passphrase=old_passphrase, legacy_iterations=iterations)

    paths = sorted(encrypted_dir.glob(f"*{ENC_SUFFIX}"))
    total = len(paths)
    print(f"migrating {total} events to the envelope format ...", flush=True)
    for done, path in enumerate(paths, start=1):
        blob = path.read_bytes()
        try:
            plaintext = cipher.decrypt(blob)
        except CryptoError as exc:
            sys.exit(f"\nerror on {path.name}: {exc}\n"
                     "(is the old passphrase the one the v1 log was encrypted with?)")
        if not is_shared_key(blob):
            _atomic_write(path, cipher.encrypt(plaintext))
        event_id = path.name[: -len(ENC_SUFFIX)]
        _atomic_write(events_dir / f"{event_id}.yaml", plaintext)
        if done % 200 == 0 or done == total:
            print(f"  {done}/{total}", flush=True)

    stale_salt = encrypted_dir / "keysalt"
    if stale_salt.exists():
        stale_salt.unlink()
    print("done. commit events-encrypted/ (re-encrypted events + keyring), "
          "then start the app with the new passphrase.")
    return 0


def rewrap(root: Path, explicit_old: str | None, iterations: int) -> int:
    new_passphrase = _new_passphrase()
    encrypted_dir = root / "events-encrypted"
    if not keyring_path(encrypted_dir).exists():
        sys.exit(f"error: no keyring at {keyring_path(encrypted_dir)} (run 'migrate' first)")
    old_passphrase = _old_passphrase(explicit_old, "Current passphrase: ")
    try:
        rewrap_keyring(encrypted_dir, old_passphrase, new_passphrase, iterations)
    except CryptoError as exc:
        sys.exit(f"error: {exc} (is the old passphrase the current one?)")
    print("passphrase changed. commit events-encrypted/keyring.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="tododo.rekey")
    parser.add_argument("--root", default=".", help="repo root holding events-encrypted/")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate_parser = subparsers.add_parser(
        "migrate", help="re-encrypt a legacy v1 log into the envelope format (expensive, one-time)")
    migrate_parser.add_argument(
        "--old-passphrase", default=None,
        help="passphrase the v1 log was encrypted with (prompted if omitted)")

    rewrap_parser = subparsers.add_parser(
        "rewrap", help="change the passphrase without re-encrypting events (cheap)")
    rewrap_parser.add_argument(
        "--old-passphrase", default=None,
        help="the current passphrase (prompted if omitted)")

    args = parser.parse_args()
    root = Path(args.root)
    if args.command == "migrate":
        return migrate(root, args.old_passphrase, args.iterations)
    return rewrap(root, args.old_passphrase, args.iterations)


if __name__ == "__main__":
    raise SystemExit(main())
