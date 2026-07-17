"""
Filewatcher process: keeps the plaintext `events/` and the git-tracked
`events-encrypted/` mirrors in sync.

Outbound: every raw `events/<id>.yaml` is encrypted to
`events-encrypted/<id>.yaml.enc` (filename stays plaintext, only contents are
encrypted). Encryption is once-only because events are immutable.

Inbound (after a git pull): any `.enc` with no plaintext counterpart is a pulled
event; it is decrypted into `events/`, parsed and streamed to `on_inbound`
(wired to `projection.apply`) so the in-memory DAG extends incrementally with no
full replay.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from tododo.crypto import Cipher
from tododo.crypto import is_shared_key
from tododo.crypto import load_or_create_salt
from tododo.models import Event

ENC_SUFFIX = ".yaml.enc"


class DecryptionProgress:
    """
    Minecraft-server-style console progress for a bulk decryption pass: a
    percentage line printed at most once per `interval_seconds`, plus a final
    100% line. Silent when there is nothing to decrypt.
    """

    def __init__(self, total: int, interval_seconds: float = 1.0):
        self.total = total
        self.interval_seconds = interval_seconds
        self.last_emit = float("-inf")
        self.last_done = -1

    def update(self, done: int) -> None:
        if self.total <= 0:
            return
        moment = time.monotonic()
        if moment - self.last_emit < self.interval_seconds:
            return
        self.last_emit = moment
        self._emit(done)

    def finish(self) -> None:
        if self.total > 0 and self.last_done != self.total:
            self._emit(self.total)

    def _emit(self, done: int) -> None:
        self.last_done = done
        percent = int(done * 100 / self.total)
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] [Tododo/INFO]: Decrypting event log: {percent}% ({done}/{self.total})", flush=True)


class FileWatcher:
    """
    Bidirectional mirror between `events_dir` and `encrypted_dir`. `on_inbound`
    is called once per newly-decrypted (pulled) event.
    """

    def __init__(
        self,
        events_dir: Path,
        encrypted_dir: Path,
        passphrase: str,
        on_inbound: Callable[[Event], None] | None = None,
        iterations: int = 200000,
    ):
        self.events_dir = Path(events_dir)
        self.encrypted_dir = Path(encrypted_dir)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.encrypted_dir.mkdir(parents=True, exist_ok=True)
        self.cipher = Cipher(passphrase, load_or_create_salt(self.encrypted_dir), iterations)
        self.on_inbound = on_inbound
        self._observer: Observer | None = None

    def enc_path_for(self, event_id: str) -> Path:
        return self.encrypted_dir / f"{event_id}{ENC_SUFFIX}"

    def raw_path_for(self, event_id: str) -> Path:
        return self.events_dir / f"{event_id}.yaml"

    @staticmethod
    def _atomic_write(destination: Path, data: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, destination)

    def encrypt_file(self, event_id: str) -> None:
        """
        Encrypt one raw event into the mirror. No-op if already mirrored.
        """
        destination = self.enc_path_for(event_id)
        if destination.exists():
            return
        source = self.raw_path_for(event_id)
        try:
            plaintext = source.read_bytes()
        except FileNotFoundError:
            return
        self._atomic_write(destination, self.cipher.encrypt(plaintext))

    def decrypt_file(self, event_id: str) -> Event:
        """
        Decrypt one mirrored event into `events/` and return the parsed `Event`.

        A legacy (per-file-salt) blob is re-encrypted in the shared-key format
        as a side effect, so a one-time ingest upgrades the whole log in place.
        """
        encrypted_path = self.enc_path_for(event_id)
        blob = encrypted_path.read_bytes()
        plaintext = self.cipher.decrypt(blob)
        if not is_shared_key(blob):
            self._atomic_write(encrypted_path, self.cipher.encrypt(plaintext))
        self._atomic_write(self.raw_path_for(event_id), plaintext)
        return Event.from_dict(yaml.safe_load(plaintext))

    def encrypt_all(self) -> None:
        """
        Mirror every raw event that has no ciphertext yet (startup / catch-up).
        """
        for path in self.events_dir.glob("*.yaml"):
            self.encrypt_file(path.stem)

    def ingest(self) -> list[Event]:
        """
        Decrypt every mirrored event missing a plaintext counterpart (a fresh
        clone's whole log, or the events pulled from git), apply each via
        `on_inbound`, and return them. Prints decryption progress once a second.
        """
        pending = [
            path for path in sorted(self.encrypted_dir.glob(f"*{ENC_SUFFIX}"))
            if not self.raw_path_for(path.name[: -len(ENC_SUFFIX)]).exists()
        ]
        ingested = []
        progress = DecryptionProgress(len(pending))
        for done, path in enumerate(pending, start=1):
            event_id = path.name[: -len(ENC_SUFFIX)]
            event = self.decrypt_file(event_id)
            if self.on_inbound:
                self.on_inbound(event)
            ingested.append(event)
            progress.update(done)
        progress.finish()
        return ingested

    def start(self) -> None:
        """
        Begin watching `events_dir` and encrypt new raw files as they land.
        """
        self.encrypt_all()
        handler = _EncryptHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.events_dir), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None


class _EncryptHandler(FileSystemEventHandler):
    def __init__(self, watcher: FileWatcher):
        self.watcher = watcher

    def _handle(self, path: str) -> None:
        name = Path(path).name
        if name.endswith(".yaml") and not name.endswith(".tmp"):
            self.watcher.encrypt_file(Path(path).stem)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)
