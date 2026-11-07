"""Django settings for the TicketForge web service (the core database owner)."""

from __future__ import annotations

from pathlib import Path

from common.env import env, env_bool, env_int, env_list

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "testserver"])

SITE_ORIGIN = env("SITE_ORIGIN", "https://localhost")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", ["https://localhost", "http://localhost"])

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "channels",
    "web.accounts",
    "web.events",
    "web.venues",
    "web.organizers",
    "web.realtime",
]

MIDDLEWARE = [
    "common.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "web.config.urls"
WSGI_APPLICATION = "web.config.wsgi.application"
ASGI_APPLICATION = "web.config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.config.context.site",
            ],
            "builtins": ["django.template.defaulttags", "django.template.defaultfilters"],
        },
    }
]

# ---------------------------------------------------------------------------
# Database (core) -- Django owns this schema through migrations.
# READ COMMITTED matches the short, row-locking transactions the checkout side
# relies on and avoids surprising REPEATABLE READ snapshot behaviour.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("MYSQL_DATABASE", "core"),
        "USER": env("MYSQL_USER", "ticketforge"),
        "PASSWORD": env("MYSQL_PASSWORD", "ticketforge"),
        "HOST": env("MYSQL_HOST", "127.0.0.1"),
        "PORT": env("MYSQL_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4", "isolation_level": "read committed"},
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_0900_ai_ci"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "web" / "static"] if (BASE_DIR / "web" / "static").exists() else []
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# ---------------------------------------------------------------------------
# Infra: Redis for cache/channel layer, Celery for background work.
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 300,
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "worker.tasks.send_*": {"queue": "emails"},
    "worker.tasks.index_event": {"queue": "search"},
    "worker.tasks.remove_event_from_index": {"queue": "search"},
    "worker.tasks.process_refund": {"queue": "payments"},
    "worker.tasks.cancel_event_refunds": {"queue": "payments"},
    "worker.tasks.reconcile_payments": {"queue": "payments"},
}

# Beat schedule: the only "cron" in the system, expressed in Celery so it is
# versioned with the code and can run on any number of beat replicas (one).
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "expire-holds": {"task": "worker.tasks.expire_holds", "schedule": 30.0},
    "reconcile-payments": {
        "task": "worker.tasks.reconcile_payments",
        "schedule": crontab(hour=2, minute=0),
    },
    "mark-finished-events": {
        "task": "worker.tasks.mark_finished_events",
        "schedule": crontab(minute=0),
    },
    "send-event-reminders": {
        "task": "worker.tasks.send_event_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_HOST = env("EMAIL_HOST", "127.0.0.1")
EMAIL_PORT = env_int("EMAIL_PORT", 1025)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "tickets@ticketforge.local")

# ---------------------------------------------------------------------------
# DRF + OpenAPI
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "web.accounts.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "web.config.errors.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"login": "5/min", "signup": "10/hour"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "TicketForge Organizer API",
    "DESCRIPTION": "Venues, events, ticket types, staff and refund handling for organizers.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------------------
# Cross-service secrets / clients
# ---------------------------------------------------------------------------
JWT_ACCESS_TTL_SECONDS = env_int("JWT_ACCESS_TTL_SECONDS", 900)
JWT_REFRESH_TTL_SECONDS = env_int("JWT_REFRESH_TTL_SECONDS", 604800)

SEARCH_ENABLED = env_bool("SEARCH_ENABLED", True)
MEILI_URL = env("MEILI_URL", "http://127.0.0.1:7700")
MEILI_MASTER_KEY = env("MEILI_MASTER_KEY", "dev-meili-master-key")
MEILI_INDEX = env("MEILI_INDEX", "events")

STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", "sk_test_xxx")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", "whsec_xxx")

CHECKOUT_DATABASE = env("CHECKOUT_DATABASE", "checkout")

# ---------------------------------------------------------------------------
# Security headers (nginx terminates TLS; these cover the Django responses too).
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", [SITE_ORIGIN, "http://localhost:8000"])
CORS_ALLOW_CREDENTIALS = True

# Admin lives behind a non-default path (spec section 12).
ADMIN_URL = env("ADMIN_URL", "admin/")

# ---------------------------------------------------------------------------
# Logging: JSON in production, human-readable when DEBUG.
# ---------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "common.logging.RequestIDFilter"}},
    "formatters": {
        "json": {"()": "common.logging.JsonFormatter"},
        "plain": {"format": "%(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "plain" if DEBUG else "json",
        }
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.db.backends": {"level": "WARNING"},
    },
}
