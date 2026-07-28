from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api_keys"
    label = "api_keys"

    def ready(self) -> None:
        # register the API-key bearer resolver so EndpointView
        # can authenticate `Authorization: Bearer mcpsk_...` without
        # apps.core importing this app (Contract 1).
        from apps.api_keys.auth_backend import authenticate_token
        from apps.core.bearer import register

        register("api_key", authenticate_token)
