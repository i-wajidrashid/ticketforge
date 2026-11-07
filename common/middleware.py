"""Request-id middleware shared by Django and (via asgi) the socket layer."""

from __future__ import annotations

import uuid

from common.logging import request_id_var


class RequestIDMiddleware:
    def __init__(self, get_response):  # type: ignore[no-untyped-def]
        self.get_response = get_response

    def __call__(self, request):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.request_id = request_id
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = request_id
        return response
