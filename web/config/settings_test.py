"""Test settings: fast hashers, in-memory channels/cache, eager Celery."""

from __future__ import annotations

import os

# Tests run with DEBUG off, like production, so the base settings insist on
# real-looking secrets. Set test-only ones before importing them.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("QR_SECRET", "test-only-qr-secret-0123456789abcdef")
# Tests never reach Stripe; CI sets the same.
os.environ.setdefault("STRIPE_GATEWAY", "fake")

from web.config.settings import *  # noqa: F403
from web.config.settings import BASE_DIR

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

SEARCH_ENABLED = False
MEDIA_ROOT = BASE_DIR / "media_test"
STATIC_ROOT = BASE_DIR / "staticfiles_test"

# Router-level throttling is exercised explicitly in its own test; leave it off
# elsewhere so unrelated tests never trip a rate limit.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"login": "1000/min", "signup": "1000/hour"},
}

# Safety: tests must never talk to a live Stripe account.
STRIPE_SECRET_KEY = "sk_test_test"
STRIPE_WEBHOOK_SECRET = "whsec_test"
