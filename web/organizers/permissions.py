"""Object-level permissions. Server-side always -- never trust the UI."""

from __future__ import annotations

from typing import Any

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView


def is_admin(user: Any) -> bool:
    return bool(
        user.is_authenticated and (user.is_superuser or getattr(user, "role", "") == "admin")
    )


class IsOrganizer(permissions.BasePermission):
    message = "Only organizer accounts can use this endpoint."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return is_admin(user) or (user.role == "organizer" and hasattr(user, "organizer_profile"))


class IsEventOwner(permissions.BasePermission):
    """Organizers may only touch their own events; admins may touch anything."""

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        if is_admin(request.user):
            return True
        organizer = getattr(obj, "organizer", None)
        return bool(organizer and organizer.user_id == request.user.pk)
