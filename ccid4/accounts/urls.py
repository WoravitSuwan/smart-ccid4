from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("2fa/setup/", views.twofa_setup, name="2fa_setup"),
    path("2fa/verify/", views.twofa_verify, name="2fa_verify"),
    path("theme/", views.set_theme, name="theme"),
    path("profile/", views.profile, name="profile"),
]
