from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("styleguide/", views.styleguide, name="styleguide"),
    path("settings/", views.settings_page, name="settings"),
    path("help/", views.help_page, name="help"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/<int:pk>/", views.notification_open, name="notification_open"),
    path("notifications/read-all/", views.notifications_read_all, name="notifications_read_all"),
]
