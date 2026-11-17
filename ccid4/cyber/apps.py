from django.apps import AppConfig


class CyberConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cyber"
    verbose_name = "คดีไซเบอร์"

    def ready(self):
        from . import signals  # noqa: F401  (keep the cross-case identifier index current)
