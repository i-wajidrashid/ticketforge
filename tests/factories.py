"""factory_boy factories: every fixture is realistic, never a hand-built dict."""

from __future__ import annotations

from datetime import timedelta

import factory
from django.utils import timezone
from web.accounts.models import Role, User
from web.events.models import (
    Category,
    Event,
    EventStatus,
    InventoryMode,
    StaffAssignment,
    TicketType,
)
from web.organizers.models import Organizer
from web.venues.models import Seat, Section, Venue


class UserFactory(factory.django.DjangoModelFactory[User]):
    class Meta:
        model = User
        django_get_or_create = ("email",)
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    role = Role.CUSTOMER
    is_verified = True

    @factory.post_generation
    def password(self, create: bool, extracted: str | None, **kwargs: object) -> None:
        self.set_password(extracted or "test-password-123")
        if create:
            self.save(update_fields=["password"])


class OrganizerFactory(factory.django.DjangoModelFactory[Organizer]):
    class Meta:
        model = Organizer

    user = factory.SubFactory(UserFactory, role=Role.ORGANIZER)
    name = factory.Sequence(lambda n: f"Organizer {n}")


class VenueFactory(factory.django.DjangoModelFactory[Venue]):
    class Meta:
        model = Venue

    name = factory.Sequence(lambda n: f"Venue {n}")
    address = "1 Main Street"
    city = "Karachi"
    country = "Pakistan"
    capacity = 120


class SectionFactory(factory.django.DjangoModelFactory[Section]):
    class Meta:
        model = Section

    venue = factory.SubFactory(VenueFactory)
    name = factory.Sequence(lambda n: f"Section {chr(65 + n % 26)}")


class SeatFactory(factory.django.DjangoModelFactory[Seat]):
    class Meta:
        model = Seat

    section = factory.SubFactory(SectionFactory)
    row = "A"
    number = factory.Sequence(lambda n: str(n + 1))


class EventFactory(factory.django.DjangoModelFactory[Event]):
    class Meta:
        model = Event

    organizer = factory.SubFactory(OrganizerFactory)
    venue = factory.SubFactory(VenueFactory)
    title = factory.Sequence(lambda n: f"Event {n}")
    description = "A night to remember."
    category = Category.MUSIC
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=7))
    ends_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=7, hours=3))
    status = EventStatus.PUBLISHED


class TicketTypeFactory(factory.django.DjangoModelFactory[TicketType]):
    class Meta:
        model = TicketType

    event = factory.SubFactory(EventFactory)
    name = factory.Sequence(lambda n: f"Type {n}")
    price_cents = 5000
    currency = "USD"
    quantity_total = 100
    max_per_order = 8
    inventory_mode = InventoryMode.GENERAL_ADMISSION


class StaffAssignmentFactory(factory.django.DjangoModelFactory[StaffAssignment]):
    class Meta:
        model = StaffAssignment

    user = factory.SubFactory(UserFactory)
    event = factory.SubFactory(EventFactory)
