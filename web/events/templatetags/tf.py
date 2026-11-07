"""Presentation helpers shared by every template (money, icons, posters)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.timesince import timesince

register = template.Library()

POSTER_VARIANTS = 4

# Stroke icons (24px grid, 1.8 stroke). Inline so they inherit currentColor and
# cost no extra request. Paths only: the wrapper adds the shared attributes.
_ICONS: dict[str, str] = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "pin": '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "alert": '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/>',
    "ticket": '<path d="M3 8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z"/><path d="M14 6v12" stroke-dasharray="2 2"/>',
    "seat": '<path d="M6 11V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v5"/><path d="M4 11h16v5H4zM6 16v4M18 16v4"/>',
    "organizer": '<path d="M3 21V9l9-6 9 6v12"/><path d="M9 21v-6h6v6"/>',
}


@register.simple_tag
def icon(name: str, label: str = "") -> SafeString:
    """``{% icon "calendar" %}`` -> decorative inline SVG (or labelled with ``label``)."""
    paths = _ICONS.get(name)
    if paths is None:
        return mark_safe("")
    aria = (
        format_html('role="img" aria-label="{}"', label)
        if label
        else mark_safe('aria-hidden="true"')
    )
    return format_html(
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" focusable="false" {}>{}</svg>',
        aria,
        mark_safe(paths),  # noqa: S308 - compile-time constant from _ICONS, never user input
    )


@register.filter
def money(cents: Any, currency: str = "USD") -> str:
    """Integer cents -> "$45" or "$45.50". Money is never a float in storage."""
    try:
        value = int(cents)
    except (TypeError, ValueError):
        return ""
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    sign = "-" if value < 0 else ""
    dollars, rest = divmod(abs(value), 100)
    return f"{sign}{symbol}{dollars:,}" if rest == 0 else f"{sign}{symbol}{dollars:,}.{rest:02d}"


@register.filter
def poster_variant(pk: Any) -> int:
    """Stable artwork per event: the same event always gets the same poster."""
    try:
        return int(pk) % POSTER_VARIANTS
    except (TypeError, ValueError):
        return 0


@register.filter
def percent(ratio: Any) -> str:
    try:
        return f"{float(ratio) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


@register.filter
def ago(moment: datetime | None) -> str:
    """'just now' for the first minute, then Django's '3 minutes ago'."""
    if moment is None:
        return ""
    if (timezone.now() - moment).total_seconds() < 60:
        return "just now"
    return f"{timesince(moment, depth=1)} ago"


@register.simple_tag
def poster_wall(count: int = 3) -> list[Any]:
    """A few real upcoming events to decorate the auth and error pages."""
    from web.events.models import Event

    try:
        return list(Event.objects.published().upcoming().select_related("venue")[:count])
    except Exception:  # the page must render even if the catalogue is unreachable
        return []
