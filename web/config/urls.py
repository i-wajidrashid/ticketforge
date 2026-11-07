"""URL layout.

Public site + organizer API are Django; the public/checkout API is FastAPI and is
proxied under ``/api/`` by nginx.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health(request: HttpRequest) -> JsonResponse:
    """Liveness for the Django container -- intentionally does not touch MySQL."""
    return JsonResponse({"status": "ok", "service": "web"})


urlpatterns = [
    path("health/", health, name="health"),
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("web.accounts.urls")),
    path("org/api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("org/api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
    path("org/api/v1/", include("web.organizers.urls")),
    path("", include("web.events.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
