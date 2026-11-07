from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "web.events"

    def ready(self) -> None:
        from web.events import signals  # noqa: F401  (registers signal handlers)
