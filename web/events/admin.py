from __future__ import annotations

from django.contrib import admin

from web.events.models import Event, StaffAssignment, TicketType


class TicketTypeInline(admin.TabularInline):  # type: ignore[type-arg]
    model = TicketType
    extra = 1


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("title", "organizer", "venue", "starts_at", "status", "category")
    list_filter = ("status", "category", "venue__city")
    search_fields = ("title", "venue__name", "organizer__name")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [TicketTypeInline]
    date_hierarchy = "starts_at"


@admin.register(TicketType)
class TicketTypeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "name",
        "event",
        "price_cents",
        "quantity_total",
        "max_per_order",
        "inventory_mode",
    )
    list_filter = ("inventory_mode", "currency")


@admin.register(StaffAssignment)
class StaffAssignmentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "event", "created_at")
    search_fields = ("user__email", "event__title")
