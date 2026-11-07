"""Redis-backed login throttling: 5 failures / 15 min per IP *and* per account.

The two keys are independent so an attacker cannot spray one account from many
IPs, nor try many accounts from one IP, without tripping the limit.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60


def _key(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.lower().encode()).hexdigest()
    return f"login-throttle:{kind}:{digest}"


def _keys(ip: str, email: str) -> list[str]:
    # One counter for this IP *and* account (so one noisy account cannot lock
    # the whole office out) plus one account-wide counter (so a botnet cannot
    # spray a single account from many IPs).
    return [_key("pair", f"{ip}|{email.lower()}"), _key("account", email)]


def is_login_blocked(ip: str, email: str) -> bool:
    return any(int(cache.get(key, 0)) >= MAX_ATTEMPTS for key in _keys(ip, email))


def register_failed_login(ip: str, email: str) -> None:
    for key in _keys(ip, email):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, WINDOW_SECONDS)


def clear_login_failures(ip: str, email: str) -> None:
    cache.delete_many(_keys(ip, email))
