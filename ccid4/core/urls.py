from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("styleguide/", views.styleguide, name="styleguide"),
    path("settings/", views.settings_page, name="settings"),
    path("help/", views.help_page, name="help"),
]
