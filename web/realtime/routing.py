from __future__ import annotations

from django.urls import re_path

from web.realtime.consumers import HoldConsumer, OrgSalesConsumer, SeatConsumer

websocket_urlpatterns = [
    re_path(r"^ws/events/(?P<event_id>\d+)/seats/?$", SeatConsumer.as_asgi()),
    re_path(r"^ws/holds/(?P<hold_id>[0-9a-fA-F-]+)/?$", HoldConsumer.as_asgi()),
    re_path(r"^ws/org/events/(?P<event_id>\d+)/sales/?$", OrgSalesConsumer.as_asgi()),
]
