from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render


@login_required
def home(request):
    return render(request, "core/home.html", {"active": "home"})


@login_required
def styleguide(request):
    return render(request, "core/styleguide.html", {"active": "", "statuses": [
        ("success", "เสร็จแล้ว"), ("warning", "ใกล้ครบกำหนด"), ("danger", "เลยกำหนด"), ("primary", "กำลังสอบสวน"), ("ai", "ร่างโดย AI – รอตรวจ"),
    ]})


@login_required
def settings_page(request):
    if request.user.role != "admin":
        raise PermissionDenied
    return render(request, "core/settings.html", {"active": "settings"})


@login_required
def help_page(request):
    return render(request, "core/help.html", {"active": "help"})
