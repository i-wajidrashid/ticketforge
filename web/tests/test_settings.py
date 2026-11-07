"""Settings refuse to run outside DEBUG with the development secrets."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

STRONG = "x" * 40


def _import_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DJANGO_DEBUG", "STRIPE_GATEWAY", "STRIPE_SECRET_KEY"}
    }
    env.update({"DJANGO_SECRET_KEY": STRONG, "JWT_SECRET": STRONG, "QR_SECRET": STRONG})
    env.update({"STRIPE_GATEWAY": "fake"})
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import web.config.settings"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_strong_secrets_start_cleanly():
    assert _import_settings().returncode == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DJANGO_SECRET_KEY", "dev-only-change-me-0123456789abcdefghijklmnop"),
        ("JWT_SECRET", "dev-jwt-secret-change-me-please-32bytes"),
        ("QR_SECRET", "too-short"),
    ],
)
def test_development_secrets_are_refused_outside_debug(name, value):
    result = _import_settings(**{name: value})
    assert result.returncode != 0
    assert name in result.stderr


def test_debug_mode_allows_the_development_values():
    result = _import_settings(DJANGO_DEBUG="1", DJANGO_SECRET_KEY="insecure-dev-key")
    assert result.returncode == 0


def test_free_checkout_is_never_reached_by_omission():
    result = _import_settings(STRIPE_GATEWAY="auto")
    assert result.returncode != 0
    assert "tickets would be free" in result.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        {"STRIPE_GATEWAY": "fake"},
        {"STRIPE_GATEWAY": "auto", "STRIPE_SECRET_KEY": "sk_test_" + "a" * 24},
        {"STRIPE_GATEWAY": "live"},
    ],
)
def test_real_keys_or_an_explicit_fake_gateway_start(overrides):
    assert _import_settings(**overrides).returncode == 0
