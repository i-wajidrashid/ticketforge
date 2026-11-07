from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from web.organizers.views import (
    EventViewSet,
    RefundViewSet,
    SeatViewSet,
    SectionViewSet,
    TicketTypeViewSet,
    VenueViewSet,
)

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("venues", VenueViewSet, basename="venue")
router.register("refunds", RefundViewSet, basename="refund")

ticket_type_actions = {
    "get": "retrieve",
    "put": "update",
    "patch": "partial_update",
    "delete": "destroy",
}
section_actions = ticket_type_actions
seat_actions = ticket_type_actions

urlpatterns = [
    path("", include(router.urls)),
    path(
        "events/<int:event_id>/ticket-types/",
        TicketTypeViewSet.as_view({"get": "list", "post": "create"}),
        name="tickettype-list",
    ),
    path(
        "events/<int:event_id>/ticket-types/<int:pk>/",
        TicketTypeViewSet.as_view(ticket_type_actions),  # type: ignore[arg-type]
        name="tickettype-detail",
    ),
    path(
        "venues/<int:venue_id>/sections/",
        SectionViewSet.as_view({"get": "list", "post": "create"}),
        name="section-list",
    ),
    path(
        "venues/<int:venue_id>/sections/<int:pk>/",
        SectionViewSet.as_view(section_actions),  # type: ignore[arg-type]
        name="section-detail",
    ),
    path(
        "sections/<int:section_id>/seats/",
        SeatViewSet.as_view({"get": "list", "post": "create"}),
        name="seat-list",
    ),
    path(
        "sections/<int:section_id>/seats/bulk/",
        SeatViewSet.as_view({"post": "bulk"}),
        name="seat-bulk",
    ),
    path(
        "sections/<int:section_id>/seats/<int:pk>/",
        SeatViewSet.as_view(seat_actions),  # type: ignore[arg-type]
        name="seat-detail",
    ),
]
