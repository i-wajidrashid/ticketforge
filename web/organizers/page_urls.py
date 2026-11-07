"""HTML routes for organizers and door staff, mounted at /org/."""

from __future__ import annotations

from django.urls import path

from web.organizers import pages

app_name = "organizer"

urlpatterns = [
    path("", pages.dashboard, name="dashboard"),
    path("scan/", pages.scan, name="scan"),
    path("events/<int:pk>/", pages.event_dashboard, name="event"),
    path("events/<int:pk>/publish/", pages.publish_event, name="publish"),
    path("events/<int:pk>/cancel/", pages.cancel_event, name="cancel"),
    path("events/<int:pk>/staff/", pages.add_staff, name="staff-add"),
    path("events/<int:pk>/staff/remove/", pages.remove_staff, name="staff-remove"),
    path("refunds/<int:refund_id>/approve/", pages.approve_refund, name="refund-approve"),
    path("refunds/<int:refund_id>/reject/", pages.reject_refund, name="refund-reject"),
]
