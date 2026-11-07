from __future__ import annotations

from django.contrib import admin

from web.venues.models import Seat, Section, Venue


class SectionInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Section
    extra = 0


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "city", "capacity")
    search_fields = ("name", "city")
    inlines = [SectionInline]


class SeatInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Seat
    extra = 0


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "venue")
    inlines = [SeatInline]


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("section", "row", "number")
    list_filter = ("section__venue",)
