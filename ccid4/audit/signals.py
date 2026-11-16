from axes.signals import user_locked_out
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import log


@receiver(user_logged_in)
def _in(sender, request, user, **kw):
    log(user, "login", "user", user.pk)


@receiver(user_logged_out)
def _out(sender, request, user, **kw):
    if user is not None:
        log(user, "logout", "user", user.pk)


@receiver(user_login_failed)
def _failed(sender, credentials, request=None, **kw):
    log(None, "login_failed", "user", credentials.get("username", "")[:60])


@receiver(user_locked_out)
def _locked(sender, request, username, ip_address, **kw):
    log(None, "locked", "user", (username or "")[:60], "ใส่รหัสผิดครบ 5 ครั้ง บัญชีถูกล็อกชั่วคราว")
