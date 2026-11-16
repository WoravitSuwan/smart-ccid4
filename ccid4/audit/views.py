from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render

from .models import ACTIONS, AuditLog
from .services import verify_chain


@login_required
def audit_list(request):
    if not request.user.can("audit.view"):
        raise PermissionDenied
    qs = AuditLog.objects.select_related("user")
    who, action, obj = request.GET.get("who", ""), request.GET.get("action", ""), request.GET.get("object", "")
    if who:
        qs = qs.filter(user_label__icontains=who)
    if action:
        qs = qs.filter(action=action)
    if obj:
        qs = qs.filter(object_id=obj)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    ok, bad = verify_chain()
    return render(
        request,
        "audit/list.html",
        {
            "page": page,
            "ok": ok,
            "bad": bad,
            "actions": ACTIONS,
            "who": who,
            "action": action,
            "object": obj,
            "active": "settings",
        },
    )
