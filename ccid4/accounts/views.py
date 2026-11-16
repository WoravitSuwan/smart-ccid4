import segno
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.services import log

from .forms import LoginForm, TokenForm
from .models import Theme


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["expired"] = self.request.GET.get("expired")
        return ctx


@require_POST
def logout_view(request):
    return auth_views.LogoutView.as_view(next_page="accounts:login")(request)


@login_required
def twofa_setup(request):
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=request.user, name="แอปยืนยันตัวตน", confirmed=False)
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if device.verify_token(form.cleaned_data["token"]):
            device.confirmed = True
            device.save()
            otp_login(request, device)
            log(request.user, "2fa_enabled", "user", request.user.pk, "เปิดใช้การยืนยันตัวตน 2 ขั้น")
            messages.success(request, "เปิดใช้การยืนยันตัวตน 2 ขั้นแล้ว")
            return redirect("core:home")
        form.add_error("token", "รหัสไม่ถูกต้อง ตรวจเวลาในโทรศัพท์แล้วลองใหม่")
    qr_svg = segno.make(device.config_url, error="m").svg_inline(scale=4, dark="#0E1A2E", light="#FFFFFF")
    return render(request, "accounts/2fa_setup.html", {"form": form, "qr_svg": qr_svg, "secret": device.key})


@login_required
def twofa_verify(request):
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        for device in TOTPDevice.objects.filter(user=request.user, confirmed=True):
            if device.verify_token(form.cleaned_data["token"]):
                otp_login(request, device)
                log(request.user, "2fa_verified", "user", request.user.pk, "ยืนยันตัวตนขั้นที่ 2 สำเร็จ")
                nxt = request.GET.get("next", "")
                return redirect(nxt if url_has_allowed_host_and_scheme(nxt, {request.get_host()}) else "core:home")
        log(request.user, "2fa_failed", "user", request.user.pk, "รหัสยืนยันตัวตนขั้นที่ 2 ไม่ถูกต้อง")
        form.add_error("token", "รหัสไม่ถูกต้อง")
    return render(request, "accounts/2fa_verify.html", {"form": form})


@login_required
def set_theme(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    theme = request.POST.get("theme")
    if theme in Theme.values:
        request.user.theme = theme
        request.user.save(update_fields=["theme"])
    nxt = request.POST.get("next", "/")
    if request.headers.get("HX-Request"):
        from django.http import HttpResponse

        return HttpResponse(status=204)
    return redirect(nxt if url_has_allowed_host_and_scheme(nxt, {request.get_host()}) else "core:home")


from .forms import ArrestTeamFormSet, CommanderFormSet, CourtFormSet, OfficerProfileForm, PersonalForm, UnitForm  # noqa: E402
from .models import OfficerProfile, Unit  # noqa: E402
from .services import SECTIONS, completeness  # noqa: E402


@login_required
def profile(request):
    user = request.user
    prof, _ = OfficerProfile.objects.get_or_create(user=user)
    unit = Unit.objects.filter(user=user).first()
    section = request.POST.get("section") if request.method == "POST" else None
    data = request.POST if section else None
    files = request.FILES if section else None
    forms_ = {
        "personal": (
            PersonalForm(data if section == "personal" else None, instance=user),
            OfficerProfileForm(data if section == "personal" else None, files if section == "personal" else None, instance=prof),
        ),
        "unit": UnitForm(data if section == "unit" else None, instance=unit),
        "commanders": CommanderFormSet(data if section == "commanders" else None, instance=user, prefix="cmd"),
        "arrest_team": ArrestTeamFormSet(data if section == "arrest_team" else None, instance=user, prefix="team"),
        "courts": CourtFormSet(data if section == "courts" else None, instance=user, prefix="court"),
    }
    if section in forms_:
        f = forms_[section]
        parts = f if isinstance(f, tuple) else (f,)
        if all(p.is_valid() for p in parts):
            for p in parts:
                obj = p.save(commit=False)
                if section == "unit":
                    obj.user = user
                if hasattr(obj, "save"):
                    obj.save()
                else:
                    p.save()
                if hasattr(p, "save_m2m"):
                    p.save_m2m()
            log(user, "edit", "profile", user.pk, f"แก้ข้อมูลพื้นฐาน: {dict(SECTIONS)[section]}")
            messages.success(request, f"บันทึก{dict(SECTIONS)[section]}แล้ว")
            return redirect(f"{request.path}#{section}")
        messages.error(request, "ยังบันทึกไม่ได้ ตรวจช่องที่มีข้อความสีแดง")
    return render(
        request, "accounts/profile.html", {"forms": forms_, "c": completeness(user), "sections": SECTIONS, "active": "profile"}
    )
