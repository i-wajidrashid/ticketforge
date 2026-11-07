"""Template context processors."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from web.config.settings import SITE_ORIGIN


def site(request: HttpRequest) -> dict[str, Any]:
    return {"site_origin": SITE_ORIGIN}
