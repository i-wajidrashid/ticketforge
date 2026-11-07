"""Request body helpers for the DRF views."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request


def json_object(request: Request) -> Mapping[str, Any]:
    """The request body as an object. A JSON array or scalar is a client error, not a crash."""
    if not isinstance(request.data, Mapping):
        raise ValidationError({"non_field_errors": "Send a JSON object."})
    return request.data
