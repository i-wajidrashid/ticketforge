"""Rebuild the Meilisearch index from scratch."""

from __future__ import annotations

from typing import Any

from common.search import configure_index, event_document, upsert_event
from django.core.management.base import BaseCommand

from web.events.models import Event, EventStatus


class Command(BaseCommand):
    help = "Rebuild the Meilisearch events index from the database."

    def handle(self, *args: Any, **options: Any) -> None:
        configure_index()
        queryset = (
            Event.objects.select_related("venue", "organizer")
            .prefetch_related("ticket_types")
            .filter(status__in=[EventStatus.PUBLISHED, EventStatus.ON_SALE, EventStatus.SOLD_OUT])
        )
        count = 0
        for event in queryset.iterator():
            upsert_event(event_document(event))
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Indexed {count} events."))
