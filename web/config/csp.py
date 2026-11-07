"""Content Security Policy for the server-rendered pages.

The pages load scripts, styles, fonts and images only from this origin, keep
page configuration in ``json_script`` data blocks (which CSP does not treat
as scripts), and use no inline event handlers or ``style`` attributes. The
policy can therefore forbid inline code entirely.

Stripe.js is allowed with the hosts Stripe lists for it and for Link in its
integration security guide: https://docs.stripe.com/security/guide

Two areas are left without a policy because they are not our markup: the
Django admin and the drf-spectacular Swagger UI, which loads from a CDN and
uses an inline bootstrap script.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

STRIPE_SCRIPTS = "https://js.stripe.com https://*.js.stripe.com"
STRIPE_FRAMES = f"{STRIPE_SCRIPTS} https://hooks.stripe.com https://link.com https://*.link.com"
STRIPE_CONNECT = "https://api.stripe.com https://link.com https://*.link.com"


def policy(host: str) -> str:
    # 'self' covers same-origin WebSockets in CSP Level 3; the explicit wss:
    # origin keeps older Safari versions working too.
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' {STRIPE_SCRIPTS}",
            "style-src 'self'",
            "img-src 'self' data: https://*.stripe.com https://*.link.com",
            "font-src 'self'",
            f"connect-src 'self' wss://{host} {STRIPE_CONNECT}",
            f"frame-src {STRIPE_FRAMES}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def _exempt(path: str) -> bool:
    return path.startswith(("/" + settings.ADMIN_URL.lstrip("/"), "/org/api/docs/"))


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if (
            response.get("Content-Type", "").startswith("text/html")
            and "Content-Security-Policy" not in response
            and not _exempt(request.path)
        ):
            response["Content-Security-Policy"] = policy(request.get_host())
        return response
