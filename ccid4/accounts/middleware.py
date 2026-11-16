from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

OPEN_PREFIXES = ("/static/", "/accounts/login", "/accounts/logout", "/accounts/2fa/")


class AccountPolicyMiddleware:
    """Signs out expired accounts and sends users who must use 2FA through it before anything else."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith(OPEN_PREFIXES):
            if user.is_expired or not user.is_active:
                logout(request)
                return redirect(reverse("accounts:login") + "?expired=1")
            if not user.is_verified():
                from django_otp.plugins.otp_totp.models import TOTPDevice

                has_device = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
                if has_device:
                    return redirect("accounts:2fa_verify")
                if user.needs_2fa():
                    return redirect("accounts:2fa_setup")
        return self.get_response(request)
