from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from web.accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):  # type: ignore[type-arg]
    ordering = ("-date_joined",)
    list_display = ("email", "role", "is_verified", "is_active", "is_staff", "date_joined")
    list_filter = ("role", "is_verified", "is_active", "is_staff")
    search_fields = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("role", "is_verified")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "role")}),
    )
