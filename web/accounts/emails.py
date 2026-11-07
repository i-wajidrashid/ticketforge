"""Account emails. Delivery is delegated to Celery so requests stay fast."""

from __future__ import annotations

import logging
from urllib.parse import quote

from django.conf import settings
from django.core import signing

logger = logging.getLogger(__name__)

VERIFY_SALT = "ticketforge.email-verify"
VERIFY_MAX_AGE = 3 * 24 * 60 * 60


def make_verification_token(user_id: int) -> str:
    return signing.dumps({"uid": user_id}, salt=VERIFY_SALT, compress=True)


def read_verification_token(token: str) -> int:
    data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
    return int(data["uid"])


def send_verification_email(user_id: int) -> None:
    from worker.tasks import send_verification_email as task

    task.delay(user_id)


def verification_url(user_id: int) -> str:
    return f"{settings.SITE_ORIGIN}/accounts/verify/{quote(make_verification_token(user_id), safe='')}/"
