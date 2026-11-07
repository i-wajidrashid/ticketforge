"""Events and ticket types: the catalogue other services read."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from web.organizers.models import Organizer
from web.venues.models import Venue


class EventStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ON_SALE = "on_sale", "On sale"
    SOLD_OUT = "sold_out", "Sold out"
    CANCELLED = "cancelled", "Cancelled"
    FINISHED = "finished", "Finished"


class Category(models.TextChoices):
    MUSIC = "music", "Music"
    TECH = "tech", "Tech"
    SPORTS = "sports", "Sports"
    ARTS = "arts", "Arts"
    COMEDY = "comedy", "Comedy"
    OTHER = "other", "Other"


class InventoryMode(models.TextChoices):
    GENERAL_ADMISSION = "ga", "General admission"
    RESERVED = "seated", "Reserved seating"


class EventQuerySet(models.QuerySet["Event"]):
    def published(self) -> EventQuerySet:
        return self.filter(
            status__in=[EventStatus.PUBLISHED, EventStatus.ON_SALE, EventStatus.SOLD_OUT]
        )

    def upcoming(self) -> EventQuerySet:
        return self.filter(ends_at__gte=timezone.now())


class Event(models.Model):
    organizer = models.ForeignKey(Organizer, on_delete=models.CASCADE, related_name="events")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True, db_index=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.DRAFT, db_index=True
    )
    cover_image = models.ImageField(upload_to="covers/%Y/%m/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        db_table = "core_event"
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["status", "starts_at"])]

    def clean(self) -> None:
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The event must end after it starts."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            base = slugify(self.title)[:200] or "event"
            slug, suffix = base, 1
            while Event.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                suffix += 1
                slug = f"{base}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def min_price_cents(self) -> int:
        prices = [t.price_cents for t in self.ticket_types.all()]
        return min(prices) if prices else 0

    @property
    def min_price_display(self) -> str:
        return f"{self.min_price_cents / 100:.2f}"

    @property
    def is_sellable(self) -> bool:
        return (
            self.status in {EventStatus.PUBLISHED, EventStatus.ON_SALE}
            and self.ends_at > timezone.now()
        )

    def status_display(self) -> tuple[str, str]:
        """(status key, label) as an organizer should read it.

        ``published`` and ``on_sale`` are both sellable; whether tickets can
        actually be bought right now depends on the ticket types' sale
        windows, so the label reflects that instead of the raw column.
        """
        if self.status not in {EventStatus.PUBLISHED, EventStatus.ON_SALE}:
            return str(self.status), str(self.get_status_display())
        now = timezone.now()
        if self.ends_at <= now:
            return "finished", "Finished"
        types = list(self.ticket_types.all())
        if any(
            (t.sale_starts_at is None or t.sale_starts_at <= now)
            and (t.sale_ends_at is None or t.sale_ends_at > now)
            for t in types
        ):
            return "on_sale", "On sale"
        if any(t.sale_starts_at and t.sale_starts_at > now for t in types):
            return "published", "Sales open later"
        return "published", "Sales closed"

    def __str__(self) -> str:
        return self.title


class TicketType(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ticket_types")
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    quantity_total = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    sale_starts_at = models.DateTimeField(null=True, blank=True)
    sale_ends_at = models.DateTimeField(null=True, blank=True)
    max_per_order = models.PositiveIntegerField(default=8, validators=[MinValueValidator(1)])
    inventory_mode = models.CharField(
        max_length=10, choices=InventoryMode.choices, default=InventoryMode.GENERAL_ADMISSION
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_ticket_type"
        unique_together = [("event", "name")]
        ordering = ["price_cents", "name"]

    def clean(self) -> None:
        if self.sale_starts_at and self.sale_ends_at and self.sale_ends_at <= self.sale_starts_at:
            raise ValidationError({"sale_ends_at": "Sales must end after they start."})

    @property
    def price_display(self) -> str:
        return f"{self.price_cents / 100:.2f}"

    def __str__(self) -> str:
        return f"{self.event.title} - {self.name}"


class StaffAssignment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_assignments"
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="staff_assignments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_staff_assignment"
        unique_together = [("user", "event")]

    def __str__(self) -> str:
        return f"{self.user.email} @ {self.event.title}"
