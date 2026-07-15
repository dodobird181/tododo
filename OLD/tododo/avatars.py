"""GitHub avatar images, keyed by git email.

A real avatar is pulled from GitHub (``avatars.githubusercontent.com``). The
account is resolved from the commit email via the GitHub user-search API; the
resolved ``login`` + ``avatar_url`` are then cached and (by the caller) written
into the board's ``authors:`` registry so they are version-controlled and only
looked up once. A known github username yields ``https://github.com/<login>.png``
directly. Anything not resolvable falls back to the monogram circle.

Fetching happens on background threads; ``resolve()`` and ``image()`` return
immediately with whatever is ready (or None), so the UI never blocks.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.parse
import urllib.request

import pygame

_SEARCH_URL = "https://api.github.com/search/users?q={q}+in:email"


def github_png_url(login: str, size: int) -> str:
    return f"https://github.com/{login}.png?size={size}"


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
        self._resolved: dict[str, object] = {}  # email -> {login, avatar_url} | None | "pending"
        self._bytes: dict[str, object] = {}      # url -> bytes | None | "pending"
        self._img: dict[tuple, object] = {}      # (url, size) -> Surface | None
        self._lock = threading.Lock()

    # --- resolve email -> github account ---------------------------------

    def resolve(self, email: str):
        """Return {'login','avatar_url'} for an email, or None if unresolved/pending."""
        if not email:
            return None
        with self._lock:
            state = self._resolved.get(email, "missing")
            if state == "missing":
                self._resolved[email] = "pending"
                threading.Thread(target=self._resolve_thread, args=(email,),
                                 name="avatar-resolve", daemon=True).start()
                return None
            if state in ("pending", None):
                return None
            return state

    def _resolve_thread(self, email: str) -> None:
        result = None
        try:
            url = _SEARCH_URL.format(q=urllib.parse.quote(email))
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "tododo",
            })
            data = json.loads(urllib.request.urlopen(req, timeout=5).read())
            items = data.get("items") or []
            if items:
                result = {"login": items[0].get("login", ""),
                          "avatar_url": items[0].get("avatar_url", "")}
        except Exception:
            result = None
        with self._lock:
            self._resolved[email] = result

    # --- fetch an avatar image from a URL --------------------------------

    def image(self, url: str, size: int):
        """Return a circular Surface for the avatar URL, or None if not ready."""
        if not url:
            return None
        key = (url, size)
        with self._lock:
            if key in self._img:
                return self._img[key]
            state = self._bytes.get(url, "missing")
            if state == "missing":
                self._bytes[url] = "pending"
                threading.Thread(target=self._fetch_thread, args=(url,),
                                 name="avatar-img", daemon=True).start()
                return None
            if state == "pending":
                return None
            if state is None:
                self._img[key] = None
                return None
            raw = state  # bytes
        surf = _circle(raw, size)
        with self._lock:
            self._img[key] = surf
        return surf

    def _fetch_thread(self, url: str) -> None:
        data = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tododo"})
            data = urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            data = None
        with self._lock:
            self._bytes[url] = data or None
