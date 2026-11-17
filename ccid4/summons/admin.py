from django.contrib import admin

from .models import RegisterStatus, Summons


@admin.register(RegisterStatus)
class RegisterStatusAdmin(admin.ModelAdmin):
    list_display = ["name", "is_final", "auto_created"]
    list_editable = ["is_final"]


@admin.register(Summons)
class SummonsAdmin(admin.ModelAdmin):
    list_display = ["register_case", "number", "issued_on", "appointment_at", "result", "created_by"]
    list_filter = ["result"]
    readonly_fields = [f.name for f in Summons._meta.fields]
