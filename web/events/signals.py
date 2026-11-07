"""Keep the search index in sync with committed event data.

The enqueue happens in ``transaction.on_commit`` so a Celery worker can never
read an event row that the request later rolled back.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from web.events.models import Event, EventStatus

logger = logging.getLogger(__name__)

INDEXABLE_STATUSES = {EventStatus.PUBLISHED, EventStatus.ON_SALE, EventStatus.SOLD_OUT}


@receiver(post_save, sender=Event)
def _on_event_saved(sender: type[Event], instance: Event, created: bool, **kwargs: object) -> None:
    if not getattr(settings, "SEARCH_ENABLED", True):
        return
    from worker.tasks import index_event, remove_event_from_index

    event_id = instance.pk

    def _dispatch() -> None:
        try:
            if instance.status in INDEXABLE_STATUSES:
                index_event.delay(event_id)
            else:
                remove_event_from_index.delay(event_id)
        except Exception:
            logger.warning("could not enqueue search index update", exc_info=True)

    transaction.on_commit(_dispatch)


@receiver(post_delete, sender=Event)
def _on_event_deleted(sender: type[Event], instance: Event, **kwargs: object) -> None:
    if not getattr(settings, "SEARCH_ENABLED", True):
        return
    from worker.tasks import remove_event_from_index

    event_id = instance.pk
    transaction.on_commit(lambda: remove_event_from_index.delay(event_id))
