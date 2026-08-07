from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.events"
    label = "events"

    def ready(self) -> None:
        # Fill in the `apps.core` hook registries. Without this every
        # `error_hook.record_error(...)` call site in the request
        # pipeline is a no-op that returns None — which is how they all
        # shipped until now, because the app upstream expected to do
        # this (`apps.observability`) was never vendored.
        #
        # Imported inside ready(), not at module scope: apps.py is
        # executed during app-registry population, before models are
        # loadable.
        from apps.core import error_hook
        from apps.events import sinks

        error_hook.register(sinks.record_error)
