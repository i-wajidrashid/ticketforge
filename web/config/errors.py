"""One error envelope for every Django/DRF response.

{"error": {"code": "...", "message": "...", "details": {}}}
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import ErrorDetail, Throttled, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def _flatten(detail: Any) -> Any:
    if isinstance(detail, ErrorDetail):
        return str(detail)
    if isinstance(detail, dict):
        return {key: _flatten(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [_flatten(item) for item in detail]
    return detail


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    if isinstance(exc, Http404):
        return Response(
            {"error": {"code": "NOT_FOUND", "message": "Not found.", "details": {}}},
            status=status.HTTP_404_NOT_FOUND,
        )

    response = exception_handler(exc, context)
    if response is None:
        logger.exception("unhandled API exception", exc_info=exc)
        return Response(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                    "details": {},
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, ValidationError):
        code, message = "VALIDATION_ERROR", "The request body is invalid."
    elif isinstance(exc, Throttled):
        code, message = "RATE_LIMITED", "Too many requests. Try again later."
    else:
        code = str(getattr(exc, "default_code", "ERROR")).upper()
        message = (
            str(exc.detail)
            if not isinstance(exc.detail, dict)
            else "The request could not be completed."
        )

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "details": _flatten(response.data),
        }
    }
    return response
