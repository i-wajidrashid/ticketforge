"""The demo seed command that a public demo runs on every deploy."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from web.accounts.models import User
from web.events.models import Event

pytestmark = pytest.mark.django_db(transaction=True)


def test_seed_fills_an_empty_database():
    call_command("seed_demo_data", "--if-empty", "--orders", "3", "--customers", "4")

    assert Event.objects.count() > 0
    assert User.objects.filter(email__startswith="fan").count() == 4


def test_seed_if_empty_leaves_existing_data_alone(seated_event):
    events_before = Event.objects.count()

    call_command("seed_demo_data", "--if-empty", "--orders", "3", "--customers", "4")

    assert Event.objects.count() == events_before
    assert not User.objects.filter(email__startswith="fan").exists()
