from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_POST


@login_required
def home(request):
    from dashboard.models import DigestSetting
    from dashboard.services import personal

    ctx = personal(request.user) if request.user.role != "admin" else {}
    digest = DigestSetting.objects.filter(user=request.user).first()
    return render(request, "core/home.html", ctx | {"digest": digest, "active": "home"})


@login_required
def styleguide(request):
    return render(
        request,
        "core/styleguide.html",
        {
            "active": "",
            "statuses": [
                ("success", "เสร็จแล้ว"),
                ("warning", "ใกล้ครบกำหนด"),
                ("danger", "เลยกำหนด"),
                ("primary", "กำลังสอบสวน"),
                ("ai", "ร่างโดย AI – รอตรวจ"),
            ],
        },
    )


@login_required
def settings_page(request):
    if request.user.role != "admin":
        raise PermissionDenied
    return render(request, "core/settings.html", {"active": "settings"})


@login_required
def help_page(request):
    return render(request, "core/help.html", {"active": "help"})


# ---------------------------------------------------------------- bell (B8: in-app notifications, never sent outside)
@login_required
def notifications(request):
    from .models import Notification

    items = list(Notification.objects.filter(user=request.user)[:20])
    return render(request, "core/_notifications.html", {"items": items})


@login_required
def notification_open(request, pk):
    from django.shortcuts import get_object_or_404, redirect
    from django.utils import timezone
    from django.utils.http import url_has_allowed_host_and_scheme

    from .models import Notification

    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if not n.read_at:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    safe = n.url if url_has_allowed_host_and_scheme(n.url, allowed_hosts={request.get_host()}) else ""
    return redirect(safe or "core:home")


@login_required
@require_POST
def notifications_read_all(request):
    from django.http import HttpResponse
    from django.utils import timezone

    from .models import Notification

    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    resp = HttpResponse("")
    resp["HX-Refresh"] = "true"
    return resp
