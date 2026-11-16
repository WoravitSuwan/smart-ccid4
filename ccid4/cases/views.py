from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audit.services import log

from . import services
from .forms import CaseCreateForm
from .models import Case, CaseStatus, CaseType


def visible(user):
    return Case.objects.visible_to(user)


def get_case(user, pk):
    return get_object_or_404(visible(user), pk=pk)


@login_required
def case_list(request):
    base = visible(request.user)
    q, status, ctype = request.GET.get("q", "").strip(), request.GET.get("status", ""), request.GET.get("type", "")
    qs = services.filter_cases(base.select_related("owner").prefetch_related("parties__person"), q, status, ctype)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "counts": services.summary_counts(base),
        "q": q,
        "status": status,
        "ctype": ctype,
        "types": CaseType.choices,
        "statuses": CaseStatus.choices,
        "active": "cases",
    }
    if request.headers.get("HX-Request"):
        return render(request, "cases/_table.html", ctx)
    return render(request, "cases/list.html", ctx)


@login_required
def case_create(request):
    if not request.user.can("case.edit"):
        raise PermissionDenied
    form = CaseCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        case = services.create_case(request.user, **form.cleaned_data)
        messages.success(request, f"สร้างคดี {case} แล้ว")
        return redirect("cases:detail", pk=case.pk)
    return render(request, "cases/create.html", {"form": form, "active": "cases"})


@login_required
@require_POST
def case_delete(request, pk):
    case = get_case(request.user, pk)
    if not (request.user.can("case.edit") and (case.owner_id == request.user.pk or request.user.can("case.view_group"))):
        raise PermissionDenied
    reason = request.POST.get("reason", "").strip()
    if request.POST.get("confirm") != case.label or not reason:
        messages.error(request, "พิมพ์เลขคดีให้ตรงและระบุเหตุผลก่อนลบ")
        return redirect("cases:list")
    services.soft_delete(request.user, case, reason)
    messages.success(request, f"ลบคดี {case} แล้ว (ผู้ดูแลระบบกู้คืนได้)")
    return redirect("cases:list")


@login_required
def case_detail(request, pk):
    case = get_case(request.user, pk)
    log(request.user, "view", "case", case.pk)
    return render(request, "cases/detail.html", {"case": case, "active": "cases"})
