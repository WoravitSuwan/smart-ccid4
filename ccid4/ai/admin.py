from django.contrib import admin

from .models import AICall


@admin.register(AICall)
class AICallAdmin(admin.ModelAdmin):
    list_display = ["started_at", "task", "backend", "model", "prompt_version", "user", "duration_ms", "attempts", "ok"]
    list_filter = ["task", "backend", "ok"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
