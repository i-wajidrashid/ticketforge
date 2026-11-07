"""Template context processors."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpRequest

from web.config.settings import SITE_ORIGIN

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@lru_cache(maxsize=1)
def asset_version() -> str:
    """Short content hash of our CSS/JS, appended as ``?v=`` for cache busting.

    Computed once per process; a deploy ships new files and a new process.
    """
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.glob("**/*")):
        if path.suffix in {".css", ".js"} and path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def site(request: HttpRequest) -> dict[str, Any]:
    """Site-wide values plus role flags that drive the navigation."""
    user = getattr(request, "user", None)
    role = getattr(user, "role", "") if user is not None and user.is_authenticated else ""
    is_admin = bool(
        user is not None and user.is_authenticated and (user.is_superuser or role == "admin")
    )
    return {
        "site_origin": SITE_ORIGIN,
        "asset_version": asset_version(),
        "nav_is_organizer": is_admin or role == "organizer",
        "nav_can_scan": is_admin or role in {"organizer", "door_staff"},
        # Local-only hints, such as where the development mail catcher is.
        "dev_hints": settings.DEBUG,
        "demo_mode": settings.DEMO_MODE,
    }
