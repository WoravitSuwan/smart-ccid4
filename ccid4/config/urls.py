from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "CCID4 · ผู้ดูแลระบบ"
admin.site.site_title = "CCID4"

urlpatterns = [
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("audit/", include("audit.urls")),
    path("cases/", include("cases.urls")),
    path("django-admin/", admin.site.urls),
]
