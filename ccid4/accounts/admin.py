from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CCID4UserAdmin(UserAdmin):
    list_display = ("username", "display_name", "role", "account_expires_at", "require_2fa", "is_active")
    list_filter = ("role", "is_active", "require_2fa")
    fieldsets = UserAdmin.fieldsets + (
        ("CCID4", {"fields": ("role", "rank", "position", "phone", "unit_group", "account_expires_at", "require_2fa", "theme")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (("CCID4", {"fields": ("role", "rank", "first_name", "last_name")}),)
