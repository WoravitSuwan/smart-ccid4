from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "audit"
    verbose_name = "บันทึกการใช้งาน"

    def ready(self):
        from . import signals  # noqa: F401
