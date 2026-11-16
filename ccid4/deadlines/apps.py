from django.apps import AppConfig


class DeadlinesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "deadlines"
    verbose_name = "กำหนดเวลาและงาน"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from .models import DeadlineRule, Holiday
        from .services import clear_cache

        for model in (DeadlineRule, Holiday):
            post_save.connect(clear_cache, sender=model, dispatch_uid=f"clear-{model.__name__}")
            post_delete.connect(clear_cache, sender=model, dispatch_uid=f"clear-del-{model.__name__}")
