from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "CCID4 · ผู้ดูแลระบบ"
admin.site.site_title = "CCID4"

urlpatterns = [
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("audit/", include("audit.urls")),
    path("cases/", include("cases.urls")),
    path("cases/", include("deadlines.urls")),
    path("cases/", include("documents.urls")),
    path("cases/", include("ai.urls")),
    path("cases/", include("dika.urls")),
    path("intake/", include("intake.urls")),
    path("summons/", include("summons.urls")),
    path("interviews/", include("interviews.urls")),
    path("cyber/", include("cyber.urls")),
    path("evidence/", include("evidence.urls")),
    path("templates/", include("documents.template_urls")),
    path("", include("intake.public_urls")),
    path("django-admin/", admin.site.urls),
]
