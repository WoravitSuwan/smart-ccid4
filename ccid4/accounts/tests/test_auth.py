from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.models import AuditLog

LOGIN = "/accounts/login/"


def login(client, username, password="pass-word-123"):
    return client.post(LOGIN, {"username": username, "password": password})


@pytest.mark.django_db
def test_login_success_is_audited(client, investigator):
    r = login(client, "inv")
    assert r.status_code == 302 and r.url == reverse("core:home")
    assert AuditLog.objects.filter(action="login", user=investigator).exists()


@pytest.mark.django_db
def test_wrong_password_five_times_locks_account(client, investigator):
    for _ in range(5):
        r = login(client, "inv", "wrong")
    r = login(client, "inv")  # even the right password is refused while locked
    assert r.status_code == 429
    assert "ถูกล็อก" in r.content.decode()
    assert AuditLog.objects.filter(action="login_failed").count() >= 5
    assert AuditLog.objects.filter(action="locked").exists()


@pytest.mark.django_db
def test_expired_account_cannot_sign_in(client, make_user):
    make_user(username="old", account_expires_at=timezone.localdate() - timedelta(days=1))
    r = login(client, "old")
    assert r.status_code == 200 and "บัญชีหมดอายุ" in r.content.decode()


@pytest.mark.django_db
def test_expired_session_user_is_signed_out(client, make_user):
    u = make_user(username="soon")
    client.force_login(u)
    u.account_expires_at = timezone.localdate() - timedelta(days=1)
    u.save()
    r = client.get("/")
    assert r.status_code == 302 and r.url.startswith("/accounts/login/")


@pytest.mark.django_db
def test_admin_must_set_up_2fa_then_verify(client, make_user):
    admin = make_user(username="adm", role="admin")
    login(client, "adm")
    r = client.get("/")
    assert r.status_code == 302 and r.url == reverse("accounts:2fa_setup")
    client.get(reverse("accounts:2fa_setup"))
    dev = TOTPDevice.objects.get(user=admin)
    token = f"{totp(dev.bin_key):06d}"
    r = client.post(reverse("accounts:2fa_setup"), {"token": token})
    assert r.status_code == 302
    dev.refresh_from_db()
    assert dev.confirmed
    assert client.get("/").status_code == 200
    # next login must pass the second step
    client.post(reverse("accounts:logout"))
    login(client, "adm")
    assert client.get("/").url == reverse("accounts:2fa_verify")
    assert client.post(reverse("accounts:2fa_verify"), {"token": "000000"}).status_code == 200
    assert AuditLog.objects.filter(action="2fa_failed").exists()


@pytest.mark.django_db
def test_idle_timeout_is_30_minutes(settings):
    assert settings.SESSION_COOKIE_AGE == 1800 and settings.SESSION_SAVE_EVERY_REQUEST


@pytest.mark.django_db
def test_theme_is_saved_per_user(client_inv, investigator):
    client_inv.post(reverse("accounts:theme"), {"theme": "light"})
    investigator.refresh_from_db()
    assert investigator.theme == "light"
    assert 'data-theme="light"' in client_inv.get("/").content.decode()


@pytest.mark.django_db
def test_role_permissions(make_user):
    clerk = make_user(role="clerk")
    commander = make_user(role="commander")
    assert clerk.can("summons.batch") and not clerk.can("audio.listen")
    assert commander.can("dashboard.unit") and not commander.can("case.edit")


@pytest.mark.django_db
def test_seed_creates_every_role(settings):
    from django.core.management import call_command

    from accounts.models import Role, User

    call_command("seed_demo")
    assert set(User.objects.values_list("role", flat=True)) == set(Role.values)
