"""Server-rendered organizer dashboard and the door scanner.

Session-authenticated HTML views over the same service layer the REST API
uses (``web.organizers.services``). Every queryset is scoped to the caller,
so one organizer can never see or act on another organizer's events.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, cast

from common.security import encode_token
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from web.accounts.models import Role, User
from web.events.models import Event, EventStatus, StaffAssignment
from web.organizers import readmodel, services
from web.organizers.charts import daily_revenue_chart
from web.organizers.permissions import is_admin

logger = logging.getLogger(__name__)

View = Callable[..., HttpResponse]
SOCKET_TOKEN_TTL = 3600
IN_FLIGHT_KEY = "refunds_in_flight"
IN_FLIGHT_SECONDS = 60


def organizer_required(view: View) -> View:
    """Logged in *and* an organizer (or admin); everyone else gets a 403."""

    @functools.wraps(view)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = cast(User, request.user)  # login_required has already run
        if not (
            is_admin(user) or (user.role == Role.ORGANIZER and hasattr(user, "organizer_profile"))
        ):
            raise PermissionDenied("The dashboard is for organizer accounts.")
        return view(request, *args, **kwargs)

    return wrapper


def _events_for(request: HttpRequest) -> Any:
    queryset = Event.objects.select_related("venue").prefetch_related("ticket_types")
    if not is_admin(request.user):
        queryset = queryset.filter(organizer__user=request.user)
    return queryset


def _owned_event(request: HttpRequest, pk: int) -> Event:
    event = _events_for(request).filter(pk=pk).first()
    if event is None:
        raise Http404("Event not found.")
    return cast(Event, event)


def _token(user: Any) -> str:
    return encode_token(user.pk, user.role, "access", ttl_seconds=SOCKET_TOKEN_TTL)


def _report(request: HttpRequest, exc: services.OrganizerActionError) -> None:
    messages.error(request, exc.message)


# ---------------------------------------------------------------- dashboard


@organizer_required
def dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    events = list(_events_for(request).order_by("starts_at"))
    overview = readmodel.events_overview([event.pk for event in events])
    capacity = {
        event.pk: sum(services.type_capacity(event, list(event.ticket_types.all())).values())
        for event in events
    }
    refunds = readmodel.refunds_for_events([event.pk for event in events])
    pending = [refund for refund in refunds if refund["status"] == "requested"]

    rows = [
        {
            "event": event,
            "stats": overview.get(event.pk, {}),
            "capacity": capacity.get(event.pk, 0),
            "sold_ratio": (overview.get(event.pk, {}).get("tickets_sold", 0) / capacity[event.pk])
            if capacity.get(event.pk)
            else 0,
        }
        for event in events
    ]
    upcoming = [row for row in rows if row["event"].ends_at >= now]
    past = [row for row in rows if row["event"].ends_at < now]
    totals = {
        "revenue_cents": sum(row["stats"].get("revenue_cents", 0) for row in rows),
        "tickets_sold": sum(row["stats"].get("tickets_sold", 0) for row in rows),
        "upcoming": len(upcoming),
        "pending_refunds": len(pending),
    }
    return render(
        request,
        "organizers/dashboard.html",
        {"upcoming": upcoming, "past": past, "totals": totals, "pending": pending[:5]},
    )


@organizer_required
def event_dashboard(request: HttpRequest, pk: int) -> HttpResponse:
    event = _owned_event(request, pk)
    ticket_types = list(event.ticket_types.all())
    sales = readmodel.event_sales(event.pk)
    sold = readmodel.sold_by_ticket_type(event.pk)
    capacity = services.type_capacity(event, ticket_types)
    type_rows = [
        {
            "type": ticket_type,
            "sold": sold.get(ticket_type.pk, 0),
            "capacity": capacity.get(ticket_type.pk, 0),
            "ratio": sold.get(ticket_type.pk, 0) / capacity[ticket_type.pk]
            if capacity.get(ticket_type.pk)
            else 0,
        }
        for ticket_type in ticket_types
    ]
    total_capacity = sum(capacity.values())
    refunds = _mark_in_flight(request, readmodel.refunds_for_events([event.pk]))
    staff = (
        StaffAssignment.objects.filter(event=event).select_related("user").order_by("user__email")
    )
    return render(
        request,
        "organizers/event.html",
        {
            "event": event,
            "sales": sales,
            "type_rows": type_rows,
            "total_capacity": total_capacity,
            "remaining": max(0, total_capacity - int(sales["tickets_sold"])),
            "chart": daily_revenue_chart(sales["sales_by_day"], timezone.localdate()),
            "recent": readmodel.recent_sales(event.pk),
            "refunds": refunds,
            "pending_refunds": [refund for refund in refunds if refund["status"] == "requested"],
            "staff": staff,
            "can_publish": event.status == EventStatus.DRAFT,
            "can_cancel": event.status
            in {
                EventStatus.PUBLISHED,
                EventStatus.ON_SALE,
                EventStatus.SOLD_OUT,
                EventStatus.DRAFT,
            },
            "dash_config": {
                "eventId": event.pk,
                "token": _token(request.user),
                "ticketsSold": int(sales["tickets_sold"]),
                "revenueCents": int(sales["revenue_cents"]),
                "capacity": total_capacity,
            },
        },
    )


# ------------------------------------------------------------------ actions


@require_POST
@organizer_required
def publish_event(request: HttpRequest, pk: int) -> HttpResponse:
    event = _owned_event(request, pk)
    try:
        services.publish_event(event)
        messages.success(request, f"Published. {event.title} is now listed and searchable.")
    except services.OrganizerActionError as exc:
        _report(request, exc)
    return redirect("organizer:event", pk=pk)


@require_POST
@organizer_required
def cancel_event(request: HttpRequest, pk: int) -> HttpResponse:
    event = _owned_event(request, pk)
    if request.POST.get("confirm_title", "").strip() != event.title:
        messages.error(request, "Type the event name exactly to confirm the cancellation.")
        return redirect("organizer:event", pk=pk)
    try:
        if services.cancel_event(event, reason=request.POST.get("reason", "")):
            messages.success(
                request, "Event cancelled. Every paid order is being refunded automatically."
            )
    except services.OrganizerActionError as exc:
        _report(request, exc)
    return redirect("organizer:event", pk=pk)


@require_POST
@organizer_required
def add_staff(request: HttpRequest, pk: int) -> HttpResponse:
    event = _owned_event(request, pk)
    try:
        assignment, created = services.assign_staff(event, request.POST.get("email", ""))
        if created:
            messages.success(
                request, f"{assignment.user.email} can now scan tickets for this event."
            )
        else:
            messages.info(request, f"{assignment.user.email} is already on the door team.")
    except services.OrganizerActionError as exc:
        _report(request, exc)
    return redirect(f"{_event_url(pk)}#door-team")


@require_POST
@organizer_required
def remove_staff(request: HttpRequest, pk: int) -> HttpResponse:
    event = _owned_event(request, pk)
    try:
        if services.remove_staff(event, request.POST.get("email", "")):
            messages.success(request, "Removed from the door team.")
    except services.OrganizerActionError as exc:
        _report(request, exc)
    return redirect(f"{_event_url(pk)}#door-team")


def _remember_in_flight(request: HttpRequest, refund_id: int) -> None:
    in_flight = dict(request.session.get(IN_FLIGHT_KEY, {}))
    in_flight[str(refund_id)] = time.time()
    request.session[IN_FLIGHT_KEY] = in_flight


def _mark_in_flight(request: HttpRequest, refunds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Show decisions the worker hasn't applied yet as "processing".

    Approving or declining only enqueues a task, so for a moment after the
    redirect the row would still say "requested" and offer the buttons again.
    The marker expires after a minute so a failed task can be retried.
    """
    in_flight: dict[str, float] = request.session.get(IN_FLIGHT_KEY, {})
    if not in_flight:
        return refunds
    now = time.time()
    still_waiting = {}
    for refund in refunds:
        sent_at = in_flight.get(str(refund["id"]))
        if sent_at and now - sent_at < IN_FLIGHT_SECONDS and refund["status"] == "requested":
            refund["status"] = "processing"
            still_waiting[str(refund["id"])] = sent_at
    request.session[IN_FLIGHT_KEY] = still_waiting
    return refunds


def _owned_refund(request: HttpRequest, refund_id: int) -> dict[str, Any]:
    refund = readmodel.refund_by_id(refund_id)
    if refund is None or not _events_for(request).filter(pk=refund["event_id"]).exists():
        raise Http404("Refund not found.")
    return refund


@require_POST
@organizer_required
def approve_refund(request: HttpRequest, refund_id: int) -> HttpResponse:
    refund = _owned_refund(request, refund_id)
    try:
        services.approve_refund(refund, request.user.pk)
        _remember_in_flight(request, refund_id)
        messages.success(
            request, "Refund approved. The customer is refunded in full within seconds."
        )
    except services.OrganizerActionError as exc:
        _report(request, exc)
    return redirect(f"{_event_url(refund['event_id'])}#refunds")


@require_POST
@organizer_required
def reject_refund(request: HttpRequest, refund_id: int) -> HttpResponse:
    refund = _owned_refund(request, refund_id)
    try:
        services.reject_refund(refund, request.user.pk, request.POST.get("reason", ""))
        _remember_in_flight(request, refund_id)
        messages.success(request, "Refund declined. The customer keeps their tickets.")
    except services.OrganizerActionError as exc:
        _report(request, exc)
    return redirect(f"{_event_url(refund['event_id'])}#refunds")


def _event_url(pk: int) -> str:
    from django.urls import reverse

    return reverse("organizer:event", kwargs={"pk": pk})


# --------------------------------------------------------------------- scan


@login_required
def scan(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if not (is_admin(user) or user.role in {Role.ORGANIZER, Role.DOOR_STAFF}):
        raise PermissionDenied("Scanning is for door staff and organizers.")
    now = timezone.now()
    if is_admin(user) or user.role == Role.ORGANIZER:
        events = _events_for(request).filter(ends_at__gte=now).order_by("starts_at")
    else:
        events = (
            Event.objects.filter(staff_assignments__user=user, ends_at__gte=now)
            .select_related("venue")
            .order_by("starts_at")
        )
    return render(
        request,
        "organizers/scan.html",
        {"events": events[:10], "scan_config": {"token": _token(user)}},
    )
