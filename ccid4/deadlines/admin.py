from django.contrib import admin

from .models import DeadlineRule, DeadlineRuleVersion, Holiday


class VersionInline(admin.TabularInline):
    model = DeadlineRuleVersion
    extra = 0
    can_delete = False
    readonly_fields = ["version", "params", "active", "changed_at", "changed_by", "note"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DeadlineRule)
class DeadlineRuleAdmin(admin.ModelAdmin):
    list_display = ["key", "name", "version", "active", "updated_at", "updated_by"]
    readonly_fields = ["key", "version", "updated_at", "updated_by"]
    fields = ["key", "name", "params", "active", "note", "version", "updated_at", "updated_by"]
    inlines = [VersionInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ["date", "name"]
    date_hierarchy = "date"
