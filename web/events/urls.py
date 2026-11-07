from __future__ import annotations

from django.urls import path

from web.events import views

app_name = "events"

urlpatterns = [
    path("", views.index, name="index"),
    path("events/<slug:slug>/", views.detail, name="detail"),
]
