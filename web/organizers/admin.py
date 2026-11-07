from __future__ import annotations

from django.contrib import admin

from web.organizers.models import Organizer


@admin.register(Organizer)
class OrganizerAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "user", "slug", "created_at")
    search_fields = ("name", "user__email")
    prepopulated_fields = {"slug": ("name",)}
