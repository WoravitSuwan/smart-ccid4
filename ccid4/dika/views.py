from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ai.llm import AIError
from audit.services import log
from cases.views import can_edit, get_case

from . import services
from .models import DikaRuling


def results(request, case, q=None, sec=None, only=False):
    auto = q is None
    if auto:
        q, sec = services.case_query(case)
    pinned_ids = set(case.pinned_rulings.values_list("ruling_id", flat=True))
    try:
        hits, error = services.search(q, sec or "", only, pinned_ids=pinned_ids), ""
    except AIError as e:
        hits, error = [], str(e)
    ctx = {
        "case": case, "hits": hits, "auto": auto, "q": "" if auto else q, "error": error,
        "pinned": case.pinned_rulings.select_related("ruling"), "editable": can_edit(request.user, case),
        "total": DikaRuling.objects.count(),
    }  # fmt: skip
    return render(request, "dika/_results.html", ctx)


@login_required
def search(request, pk):
    case = get_case(request.user, pk)
    q = request.GET.get("dika_q")
    if q is not None:
        q = q.strip()
        log(request.user, "view", "dika", "", f"ค้นฎีกา คดี {case}")
    return results(request, case, q if q else None, request.GET.get("dika_sec", ""), request.GET.get("dika_only") == "on")


@login_required
@require_POST
def pin(request, pk, ruling_id):
    case = get_case(request.user, pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    ruling = get_object_or_404(DikaRuling, pk=ruling_id)
    if case.pinned_rulings.filter(ruling=ruling).exists():
        services.unpin(request.user, case, ruling.pk)
    else:
        services.pin(request.user, case, ruling)
    return results(request, case)
