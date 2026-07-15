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
from pathlib import Path
from typing import Callable

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from tododo.crypto import decrypt
from tododo.crypto import encrypt
from tododo.models import Event

ENC_SUFFIX = ".yaml.enc"


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
        self.passphrase = passphrase
        self.on_inbound = on_inbound
        self.iterations = iterations
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
        self._atomic_write(destination, encrypt(plaintext, self.passphrase, self.iterations))

    def decrypt_file(self, event_id: str) -> Event:
        """
        Decrypt one mirrored event into `events/` and return the parsed `Event`.
        """
        blob = self.enc_path_for(event_id).read_bytes()
        plaintext = decrypt(blob, self.passphrase, self.iterations)
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
        Decrypt every mirrored event missing a plaintext counterpart (i.e. pulled
        from git), apply each via `on_inbound`, and return them.
        """
        ingested = []
        for path in sorted(self.encrypted_dir.glob(f"*{ENC_SUFFIX}")):
            event_id = path.name[: -len(ENC_SUFFIX)]
            if self.raw_path_for(event_id).exists():
                continue
            event = self.decrypt_file(event_id)
            if self.on_inbound:
                self.on_inbound(event)
            ingested.append(event)
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
