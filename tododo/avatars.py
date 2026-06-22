"""Avatar images fetched from the web, keyed by git email.

A real avatar can't be derived from an email via GitHub (that needs the API and a
username), but **Gravatar** is keyed directly on the email's md5 hash and many
developers — including GitHub users — have one. We request it with ``d=404`` so a
missing avatar returns an error and we cleanly fall back to the monogram circle.

Fetching happens on background threads; ``get()`` returns a ready circular Surface
or None (draw the monogram meanwhile). Results are cached, including misses.
"""

from __future__ import annotations

import hashlib
import io
import threading
import urllib.request

import pygame


def gravatar_url(email: str, size: int) -> str:
    digest = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    return f"https://www.gravatar.com/avatar/{digest}?s={size}&d=404"


def _circle(data: bytes, d: int):
    try:
        img = pygame.image.load(io.BytesIO(data)).convert_alpha()
    except Exception:
        return None
    img = pygame.transform.smoothscale(img, (d, d))
    mask = pygame.Surface((d, d), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), (d // 2, d // 2), d // 2)
    out = pygame.Surface((d, d), pygame.SRCALPHA)
    out.blit(img, (0, 0))
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return out


class AvatarStore:
    def __init__(self):
        self._bytes: dict[str, object] = {}   # email -> bytes | None | "pending"
        self._surf: dict[tuple, object] = {}  # (email, size) -> Surface | None
        self._lock = threading.Lock()

    def _fetch(self, email: str, px: int) -> None:
        data = None
        try:
            req = urllib.request.Request(gravatar_url(email, px), headers={"User-Agent": "tododo"})
            data = urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            data = None
        with self._lock:
            self._bytes[email] = data or None

    def get(self, email: str, size: int):
        """Return a circular Surface for the email, or None if not ready/available."""
        if not email:
            return None
        key = (email, size)
        with self._lock:
            if key in self._surf:
                return self._surf[key]
            state = self._bytes.get(email, "missing")
            if state == "missing":
                self._bytes[email] = "pending"
                threading.Thread(target=self._fetch, args=(email, size * 2),
                                 name="avatar", daemon=True).start()
                return None
            if state == "pending":
                return None
            if state is None:
                self._surf[key] = None
                return None
            raw = state  # bytes
        # Build the Surface on the calling (main) thread.
        surf = _circle(raw, size)
        with self._lock:
            self._surf[key] = surf
        return surf
