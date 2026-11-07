"""Organizer API (Django REST Framework).

Every queryset is scoped to the caller's organizer; object lookups can therefore
never leak another organizer's events.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from web.events.models import Event, EventStatus, StaffAssignment, TicketType
from web.organizers import services
from web.organizers.models import Organizer
from web.organizers.permissions import IsOrganizer, is_admin
from web.organizers.readmodel import event_sales, refund_by_id, refunds_for_events
from web.organizers.serializers import (
    EventCancelSerializer,
    EventSerializer,
    RefundSerializer,
    SeatSerializer,
    SectionSerializer,
    StaffAssignmentSerializer,
    TicketTypeSerializer,
    VenueSerializer,
)
from web.organizers.services import OrganizerActionError
from web.venues.models import Seat, Section, Venue

logger = logging.getLogger(__name__)


@contextmanager
def _as_validation_error() -> Iterator[None]:
    """Translate a shared-service rule violation into DRF's 400 envelope."""
    try:
        yield
    except OrganizerActionError as exc:
        raise ValidationError({exc.field: exc.message}) from exc


def caller_organizer(request: Request) -> Organizer:
    organizer = getattr(request.user, "organizer_profile", None)
    if organizer is None:
        raise PermissionDenied("This account has no organizer profile.")
    return cast(Organizer, organizer)


def scoped_event(request: Request, event_id: int) -> Event:
    queryset = Event.objects.select_related("organizer", "venue")
    if not is_admin(request.user):
        queryset = queryset.filter(organizer__user=request.user)
    event = queryset.filter(pk=event_id).first()
    if event is None:
        raise NotFound("Event not found.")
    return event


class EventViewSet(viewsets.ModelViewSet[Event]):
    serializer_class = EventSerializer
    permission_classes = [IsOrganizer]

    def get_queryset(self):  # type: ignore[no-untyped-def]
        queryset = Event.objects.select_related("organizer", "venue").prefetch_related(
            "ticket_types"
        )
        if not is_admin(self.request.user):
            queryset = queryset.filter(organizer__user=self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        search = self.request.query_params.get("q")
        if search:
            queryset = queryset.filter(title__icontains=search)
        return queryset

    def perform_create(self, serializer: EventSerializer) -> None:  # type: ignore[override]
        serializer.save(organizer=caller_organizer(self.request))

    @action(detail=True, methods=["post"])
    def publish(self, request: Request, pk: str | None = None) -> Response:
        event = self.get_object()
        with _as_validation_error():
            services.publish_event(event)
        return Response({"status": event.status, "indexed": True}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        event = self.get_object()
        payload = EventCancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        with _as_validation_error():
            cancelled = services.cancel_event(
                event, reason=payload.validated_data.get("reason", "")
            )
        if not cancelled:
            return Response({"status": event.status, "refunds_enqueued": False})
        return Response(
            {
                "status": event.status,
                "refunds_enqueued": True,
                "reason": payload.validated_data.get("reason", ""),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"])
    def sales(self, request: Request, pk: str | None = None) -> Response:
        event = self.get_object()
        return Response(event_sales(event.pk))

    @action(detail=True, methods=["get", "post", "delete"], url_path="staff")
    def staff(self, request: Request, pk: str | None = None) -> Response:
        event = self.get_object()
        if request.method == "GET":
            assignments = StaffAssignment.objects.filter(event=event).select_related("user")
            return Response(StaffAssignmentSerializer(assignments, many=True).data)

        email = str(request.data.get("email", ""))
        if request.method == "POST":
            with _as_validation_error():
                assignment, created = services.assign_staff(event, email)
            return Response(
                StaffAssignmentSerializer(assignment).data,
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        with _as_validation_error():
            deleted = services.remove_staff(event, email)
        return Response({"deleted": deleted}, status=status.HTTP_200_OK)


class TicketTypeViewSet(viewsets.ModelViewSet[TicketType]):
    serializer_class = TicketTypeSerializer
    permission_classes = [IsOrganizer]

    def get_event(self) -> Event:
        if not hasattr(self, "_event"):
            self._event = scoped_event(self.request, int(self.kwargs["event_id"]))
        return self._event

    def get_queryset(self):  # type: ignore[no-untyped-def]
        return TicketType.objects.filter(event=self.get_event())

    def perform_create(self, serializer: TicketTypeSerializer) -> None:  # type: ignore[override]
        serializer.save(event=self.get_event())

    def perform_destroy(self, instance: TicketType) -> None:
        instance.delete()


def check_venue_write(request: Request, venue: Venue, *, changes_layout: bool = False) -> None:
    """Venues are shared, so writes to one are guarded.

    Every organizer can read venues and create new ones. Changing a venue, or
    its sections and seats, is limited to admins and to an organizer whose
    events are the only ones there. Once a venue has an event past draft, its
    existing seats and sections are frozen for everyone: tickets point at those
    seat rows, so renaming or deleting one would rewrite or orphan tickets that
    were already sold. Adding new seats and sections stays allowed.
    """
    if request.method in permissions.SAFE_METHODS:
        return
    if not is_admin(request.user) and venue.events.exclude(organizer__user=request.user).exists():
        raise PermissionDenied(
            "Another organizer has events at this venue, so only an admin can change it."
        )
    if changes_layout and venue.events.exclude(status=EventStatus.DRAFT).exists():
        raise PermissionDenied(
            "This venue has published events. Add new seats or sections instead of "
            "changing or removing existing ones."
        )


class VenueViewSet(viewsets.ModelViewSet[Venue]):
    serializer_class = VenueSerializer
    permission_classes = [IsOrganizer]
    queryset = Venue.objects.all()
    search_fields = ["name", "city"]

    def perform_update(self, serializer: VenueSerializer) -> None:  # type: ignore[override]
        check_venue_write(self.request, cast(Venue, serializer.instance))
        serializer.save()

    def perform_destroy(self, instance: Venue) -> None:
        check_venue_write(self.request, instance)
        if instance.events.exists():
            raise PermissionDenied("Events are scheduled at this venue, so it can't be deleted.")
        instance.delete()


class SectionViewSet(viewsets.ModelViewSet[Section]):
    serializer_class = SectionSerializer
    permission_classes = [IsOrganizer]

    def get_venue(self) -> Venue:
        return get_object_or_404(Venue, pk=self.kwargs["venue_id"])

    def get_queryset(self):  # type: ignore[no-untyped-def]
        return Section.objects.filter(venue=self.get_venue()).prefetch_related("seats")

    def perform_create(self, serializer: SectionSerializer) -> None:  # type: ignore[override]
        venue = self.get_venue()
        check_venue_write(self.request, venue)
        serializer.save(venue=venue)

    def perform_update(self, serializer: SectionSerializer) -> None:  # type: ignore[override]
        check_venue_write(self.request, self.get_venue(), changes_layout=True)
        serializer.save()

    def perform_destroy(self, instance: Section) -> None:
        check_venue_write(self.request, self.get_venue(), changes_layout=True)
        instance.delete()


class SeatViewSet(viewsets.ModelViewSet[Seat]):
    serializer_class = SeatSerializer
    permission_classes = [IsOrganizer]

    def get_section(self) -> Section:
        return get_object_or_404(
            Section.objects.select_related("venue"), pk=self.kwargs["section_id"]
        )

    def get_queryset(self):  # type: ignore[no-untyped-def]
        return Seat.objects.filter(section=self.get_section())

    def perform_create(self, serializer: SeatSerializer) -> None:  # type: ignore[override]
        section = self.get_section()
        check_venue_write(self.request, section.venue)
        serializer.save(section=section)

    def perform_update(self, serializer: SeatSerializer) -> None:  # type: ignore[override]
        check_venue_write(self.request, self.get_section().venue, changes_layout=True)
        serializer.save()

    def perform_destroy(self, instance: Seat) -> None:
        check_venue_write(self.request, self.get_section().venue, changes_layout=True)
        instance.delete()

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request: Request, section_id: str | None = None) -> Response:
        """Bulk-load a seat map from CSV: ``row,number`` per line (header optional)."""
        section = self.get_section()
        check_venue_write(request, section.venue)
        raw = request.data.get("csv")
        if raw is None and request.FILES.get("file"):
            raw = request.FILES["file"].read().decode("utf-8")
        if not raw:
            raise ValidationError({"csv": "Provide CSV text or upload a file."})

        rows: list[dict[str, Any]]
        dict_reader = csv.DictReader(io.StringIO(raw.strip()))
        if dict_reader.fieldnames and {"row", "number"}.issubset(
            {field.strip() for field in dict_reader.fieldnames}
        ):
            rows = list(dict_reader)
        else:
            plain_reader = csv.reader(io.StringIO(raw.strip()))
            rows = [
                {"row": row[0], "number": row[1]}
                for row in plain_reader
                if len(row) >= 2 and row[0].lower() != "row"
            ]

        if len(rows) > 5000:
            raise ValidationError({"csv": "At most 5000 seats per upload."})

        seats = []
        seen: set[tuple[str, str]] = set()
        for index, row in enumerate(rows, start=2):
            row_label = str(row.get("row", "")).strip()
            number = str(row.get("number", "")).strip()
            if not row_label or not number:
                raise ValidationError({"csv": f"Line {index}: row and number are required."})
            if len(row_label) > 10 or len(number) > 10:
                raise ValidationError({"csv": f"Line {index}: values are too long."})
            if (row_label, number) in seen:
                raise ValidationError({"csv": f"Line {index}: duplicate seat {row_label}{number}."})
            seen.add((row_label, number))
            seats.append(Seat(section=section, row=row_label, number=number))

        with transaction.atomic():
            Seat.objects.bulk_create(seats, ignore_conflicts=True, batch_size=500)
        return Response(
            {
                "submitted": len(seats),
                "total_for_section": Seat.objects.filter(section=section).count(),
            },
            status=status.HTTP_201_CREATED,
        )


class RefundViewSet(viewsets.ViewSet):
    """Refund approvals. Writes happen in Celery via the checkout service code."""

    permission_classes = [IsOrganizer]

    def _event_ids(self) -> list[int]:
        queryset = Event.objects.all()
        if not is_admin(self.request.user):
            queryset = queryset.filter(organizer__user=self.request.user)
        return list(queryset.values_list("id", flat=True))

    def list(self, request: Request) -> Response:
        refunds = refunds_for_events(self._event_ids())
        return Response(RefundSerializer(refunds, many=True).data)

    def _owned_refund(self, pk: str) -> dict[str, Any]:
        refund = refund_by_id(int(pk))
        if refund is None or refund["event_id"] not in self._event_ids():
            raise NotFound("Refund not found.")
        return refund

    @action(detail=True, methods=["post"])
    def approve(self, request: Request, pk: str | None = None) -> Response:
        refund = self._owned_refund(str(pk))
        with _as_validation_error():
            services.approve_refund(refund, request.user.pk)
        return Response({"status": "processing"}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def reject(self, request: Request, pk: str | None = None) -> Response:
        refund = self._owned_refund(str(pk))
        with _as_validation_error():
            services.reject_refund(refund, request.user.pk, str(request.data.get("reason", "")))
        return Response({"status": "rejected"}, status=status.HTTP_202_ACCEPTED)
