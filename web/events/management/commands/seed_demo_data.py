"""Generate a realistic demo dataset (events, customers, paid orders, refunds).

Run inside the web/api container:  python manage.py seed_demo_data --orders 400
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from typing import Any

from api.app.db import session_scope
from api.app.models import Ticket, utcnow
from api.app.services import holds, payments
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from sqlalchemy import text

from web.accounts.models import Role, User
from web.events.models import Category, Event, EventStatus, InventoryMode, TicketType
from web.organizers.models import Organizer
from web.venues.models import Seat, Section, Venue

ORGANIZER_EMAIL = "organizer@ticketforge.local"
PASSWORD = "ticketforge123"

EVENT_BLUEPRINTS = [
    ("Neon Skyline", Category.MUSIC, "Metro Arts Center", 14, InventoryMode.RESERVED, 6500),
    ("Midnight Static", Category.MUSIC, "Skyline Arena", 21, InventoryMode.GENERAL_ADMISSION, 3500),
    (
        "Ship It: Platform Engineering",
        Category.TECH,
        "Metro Arts Center",
        9,
        InventoryMode.RESERVED,
        9000,
    ),
    (
        "Laugh Track Live",
        Category.COMEDY,
        "Comedy Cellar Karachi",
        5,
        InventoryMode.GENERAL_ADMISSION,
        2500,
    ),
    ("Abstract Futures", Category.ARTS, "Metro Arts Center", 30, InventoryMode.RESERVED, 4500),
    ("City Derby", Category.SPORTS, "Skyline Arena", 45, InventoryMode.GENERAL_ADMISSION, 2000),
    (
        "Synthwave Sessions",
        Category.MUSIC,
        "Skyline Arena",
        60,
        InventoryMode.GENERAL_ADMISSION,
        3000,
    ),
    ("Founders Fireside", Category.TECH, "Metro Arts Center", 3, InventoryMode.RESERVED, 7500),
]

DESCRIPTIONS = {
    "Neon Skyline": (
        "A two-hour synth-pop set with a full light rig. Reserved seating in the Stalls "
        "and on the Balcony. Doors open 45 minutes before the show."
    ),
    "Midnight Static": (
        "Loud guitars and small-room energy in a big room. Standing only, so arrive "
        "early for a spot near the front."
    ),
    "Ship It: Platform Engineering": (
        "An evening of talks on running production systems: deploy pipelines, on-call "
        "that doesn't burn people out, and what actually breaks at scale. Seated, with "
        "questions after each talk."
    ),
    "Laugh Track Live": "Five comics, one host, ninety minutes. Some material is for adults.",
    "Abstract Futures": (
        "Three of the exhibiting artists talk through their new work, followed by an "
        "open floor. Reserved seats for the panel."
    ),
    "City Derby": (
        "The biggest local fixture of the season. General admission terraces, and the "
        "gates open 90 minutes before kick-off."
    ),
    "Synthwave Sessions": "Retro synths, analogue drum machines and a late finish. Standing only.",
    "Founders Fireside": (
        "Three founders on what they would do differently, in an unscripted conversation "
        "with the audience. Reserved seating."
    ),
}

SHOW_TIME = time(19, 30)  # venue-local
SHOW_LENGTH = timedelta(hours=3)


def evening(days_from_now: int) -> datetime:
    """19:30 venue-local time, ``days_from_now`` days ahead (stored as UTC)."""
    day = timezone.localdate() + timedelta(days=days_from_now)
    return timezone.make_aware(datetime.combine(day, SHOW_TIME))


class Command(BaseCommand):
    help = "Seed demo organizers, venues, events, customers and paid orders."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--orders", type=int, default=300, help="Number of paid orders to simulate."
        )
        parser.add_argument("--customers", type=int, default=120, help="Number of demo customers.")
        parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
        parser.add_argument(
            "--reschedule",
            action="store_true",
            help="Move existing demo events to fresh evening dates and refresh their copy.",
        )
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="Do nothing when any event exists, so a deploy can run this on every start.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["if_empty"] and Event.objects.exists():
            self.stdout.write("Events already exist, so the demo data was not seeded.")
            return
        rng = random.Random(options["seed"])
        organizer = self._organizer()
        venues = self._venues()
        events = self._events(organizer, venues, reschedule=options["reschedule"])
        customers = self._customers(options["customers"])
        paid, abandoned = self._orders(rng, events, customers, options["orders"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(events)} events, {len(customers)} customers, "
                f"{paid} paid orders and {abandoned} abandoned holds."
            )
        )
        self.stdout.write(f"Organizer login: {ORGANIZER_EMAIL} / {PASSWORD}")

    # ------------------------------------------------------------------

    def _organizer(self) -> Organizer:
        user, created = User.objects.get_or_create(
            email=ORGANIZER_EMAIL,
            defaults={"role": Role.ORGANIZER, "is_verified": True, "is_staff": True},
        )
        if created:
            user.set_password(PASSWORD)
            user.save(update_fields=["password"])
        organizer, _ = Organizer.objects.get_or_create(user=user, defaults={"name": "Forge Live"})
        return organizer

    def _venues(self) -> dict[str, Venue]:
        metro, _ = Venue.objects.get_or_create(
            name="Metro Arts Center",
            defaults={
                "address": "12 Arts Avenue",
                "city": "Karachi",
                "country": "Pakistan",
                "capacity": 240,
            },
        )
        if not metro.sections.exists():
            for section_name, rows in (("Stalls", ["A", "B", "C"]), ("Balcony", ["D", "E"])):
                section = Section.objects.create(venue=metro, name=section_name)
                Seat.objects.bulk_create(
                    [
                        Seat(section=section, row=row, number=str(number))
                        for row in rows
                        for number in range(1, 16)
                    ]
                )

        arena, _ = Venue.objects.get_or_create(
            name="Skyline Arena",
            defaults={
                "address": "88 Skyline Road",
                "city": "Karachi",
                "country": "Pakistan",
                "capacity": 800,
            },
        )
        comedy, _ = Venue.objects.get_or_create(
            name="Comedy Cellar Karachi",
            defaults={
                "address": "5 Old Town Lane",
                "city": "Karachi",
                "country": "Pakistan",
                "capacity": 120,
            },
        )
        return {"Metro Arts Center": metro, "Skyline Arena": arena, "Comedy Cellar Karachi": comedy}

    def _events(
        self, organizer: Organizer, venues: dict[str, Venue], *, reschedule: bool = False
    ) -> list[Event]:
        events: list[Event] = []
        for title, category, venue_name, days, mode, price in EVENT_BLUEPRINTS:
            event, created = Event.objects.get_or_create(
                slug=slugify(title),
                defaults={
                    "organizer": organizer,
                    "venue": venues[venue_name],
                    "title": title,
                    "description": DESCRIPTIONS.get(title, ""),
                    "category": category,
                    "starts_at": evening(days),
                    "ends_at": evening(days) + SHOW_LENGTH,
                    "status": EventStatus.PUBLISHED if days > 10 else EventStatus.ON_SALE,
                },
            )
            if reschedule and not created:
                event.description = DESCRIPTIONS.get(title, event.description)
                event.starts_at = evening(days)
                event.ends_at = event.starts_at + SHOW_LENGTH
                for ticket_type in event.ticket_types.all():
                    ticket_type.sale_ends_at = event.starts_at - timedelta(hours=1)
                    ticket_type.save(update_fields=["sale_ends_at"])
                event.save(update_fields=["description", "starts_at", "ends_at", "updated_at"])
            if created:
                TicketType.objects.create(
                    event=event,
                    name="General" if mode == InventoryMode.GENERAL_ADMISSION else "Stalls",
                    price_cents=price,
                    currency="USD",
                    quantity_total=400 if mode == InventoryMode.GENERAL_ADMISSION else 65,
                    max_per_order=4,
                    inventory_mode=mode,
                    sale_ends_at=event.starts_at - timedelta(hours=1),
                )
                if mode == InventoryMode.RESERVED:
                    TicketType.objects.create(
                        event=event,
                        name="Balcony",
                        price_cents=max(1500, price - 2000),
                        currency="USD",
                        quantity_total=30,
                        max_per_order=4,
                        inventory_mode=mode,
                        sale_ends_at=event.starts_at - timedelta(hours=1),
                    )
            events.append(event)
        return events

    def _customers(self, count: int) -> list[User]:
        users: list[User] = []
        for index in range(count):
            email = f"fan{index:03d}@ticketforge.local"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"role": Role.CUSTOMER, "is_verified": True},
            )
            if created:
                user.set_password(PASSWORD)
                user.save(update_fields=["password"])
            users.append(user)
        return users

    def _orders(
        self, rng: random.Random, events: list[Event], customers: list[User], target: int
    ) -> tuple[int, int]:
        paid = 0
        abandoned = 0
        attempts = 0
        while paid < target and attempts < target * 4:
            attempts += 1
            event = rng.choice(events)
            customer = rng.choice(customers)
            ticket_types = list(event.ticket_types.all())
            if not ticket_types:
                continue
            ticket_type = rng.choice(ticket_types)

            try:
                with session_scope() as session:
                    if ticket_type.inventory_mode == InventoryMode.RESERVED:
                        seats = list(
                            Seat.objects.filter(section__venue=event.venue).order_by("?")[
                                : rng.choice([1, 2])
                            ]
                        )
                        if not seats:
                            continue
                        hold = holds.create_hold(
                            session,
                            user_id=customer.pk,
                            event_id=event.pk,
                            seat_ids=[seat.id for seat in seats],
                            ticket_type_id=ticket_type.pk,
                        )
                    else:
                        hold = holds.create_hold(
                            session,
                            user_id=customer.pk,
                            event_id=event.pk,
                            ticket_type_id=ticket_type.pk,
                            quantity=rng.choice([1, 1, 2]),
                        )

                    # 20% of customers abandon checkout, which fuels the drop-off report.
                    if rng.random() < 0.2:
                        session.execute(
                            text("UPDATE hold SET created_at = :created WHERE id = :id"),
                            {
                                "created": utcnow() - timedelta(days=rng.randint(0, 20)),
                                "id": hold.id,
                            },
                        )
                        session.commit()
                        abandoned += 1
                        continue

                    payment = payments.start_payment(
                        session,
                        user_id=customer.pk,
                        hold_id=hold.id,
                        idempotency_key=f"seed-{event.pk}-{customer.pk}-{attempts}",
                    )
                    payments.handle_webhook_event(
                        session,
                        stripe_event_id=f"evt_seed_{attempts}",
                        event_type="payment_intent.succeeded",
                        payload={
                            "id": f"evt_seed_{attempts}",
                            "type": "payment_intent.succeeded",
                            "data": {"object": {"id": payment.payment_intent_id}},
                        },
                    )
                    # Backdate a slice of orders so the notebook has a time series.
                    session.execute(
                        text("UPDATE orders SET created_at = :created WHERE id = :id"),
                        {
                            "created": utcnow()
                            - timedelta(days=rng.randint(0, 25), hours=rng.randint(0, 23)),
                            "id": payment.order_id,
                        },
                    )
                    session.commit()
                    paid += 1

                    if rng.random() < 0.45:
                        with session_scope() as inner:
                            tickets = list(
                                inner.query(Ticket)
                                .filter(Ticket.order_id == payment.order_id)
                                .all()
                            )
                            for ticket in tickets:
                                ticket.status = "checked_in"
                                ticket.checked_in_at = utcnow()
                                ticket.checked_in_by = customer.pk
                            inner.commit()
            except Exception:
                continue
        return paid, abandoned
