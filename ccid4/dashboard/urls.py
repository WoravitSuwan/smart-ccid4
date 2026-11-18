from django.urls import path

from . import views

app_name = "dashboard"
urlpatterns = [
    path("", views.unit, name="unit"),
    path("export/<str:fmt>/", views.export, name="export"),
    path("digest/", views.digest_setting, name="digest"),
]
