"""Celery application shared by the worker and beat containers."""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web.config.settings")

import django

django.setup()

celery_app = Celery("ticketforge")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")
celery_app.autodiscover_tasks(["worker"])

# Best practice: a worker that dies mid-task must not silently drop the message.
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
)
