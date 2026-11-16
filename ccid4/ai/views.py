import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from cases.views import can_edit, get_case
from parties.models import CaseParty, Statement

from . import services
from .llm import AIError

TAB_OF = {"accuser": "accuser", "suspect": "suspects", "witness": "witnesses"}


def editable(request, pk):
    case = get_case(request.user, pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    return case


def back_to(case, tab) -> HttpResponse:
    """HTMX: reload the case page on the right tab (the page is clean: AI buttons are locked while unsaved)."""
    # The address bar already holds #tab (tabs keep it in sync), and a same-page redirect would not reload.
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


def ai_error(request, e):
    return render(request, "ai/_error.html", {"message": str(e)})


@login_required
@require_POST
def charges(request, pk):
    case = editable(request, pk)
    try:
        out = services.analyze_charges(case, request.user)
    except AIError as e:
        return ai_error(request, e)
    from cases.models import CourtType

    apply = {f"c-{k}": out[k] for k in services.CHARGE_FIELDS}
    rows = [
        ("ฐานความผิด", out["offense"]), ("กฎหมายและมาตรา", out["law_sections"]), ("อัตราโทษ", out["penalty_text"]),
        ("โทษจำคุกสูงสุด", f"{out['max_penalty_months']} เดือน"), ("โทษปรับสูงสุด", f"{out['max_fine']:,} บาท"),
        ("ประเภทศาล", dict(CourtType.choices).get(out["court_type"], out["court_type"])), ("เขตอำนาจศาล", out["jurisdiction"]),
    ]  # fmt: skip
    ctx = {"rows": rows, "reasoning": out["reasoning"], "apply": json.dumps(apply, ensure_ascii=False)}
    return render(request, "ai/_charges.html", ctx)


@login_required
@require_POST
def summary(request, pk):
    case = editable(request, pk)
    try:
        text = services.summarize(case, request.user)
    except AIError as e:
        return ai_error(request, e)
    return render(
        request,
        "ai/_summary.html",
        {"text": text, "apply": json.dumps({"c-circumstances": text, "circ_ai": text}, ensure_ascii=False)},
    )


@login_required
@require_POST
def draft(request, pk, party_id):
    case = editable(request, pk)
    cp = get_object_or_404(CaseParty.objects.select_related("person"), pk=party_id, case=case)
    plea = request.POST.get("plea") if request.POST.get("plea") in services.PLEAS else (cp.plea or "deny")
    try:
        services.draft_statement(cp, request.user, plea if cp.role == "suspect" else None)
    except AIError as e:
        return ai_error(request, e)
    messages.success(request, "AI ร่างคำให้การแล้ว (ร่างโดย AI – รอตรวจ) ตรวจแก้แล้วกดยืนยัน")
    return back_to(case, TAB_OF[cp.role])


@login_required
@require_POST
def confirm(request, pk, statement_id):
    case = get_case(request.user, pk)
    st = get_object_or_404(Statement.objects.select_related("party__person"), pk=statement_id, party__case=case)
    if not (request.user.can("statement.confirm") and can_edit(request.user, case)):
        raise PermissionDenied
    services.confirm_statement(st, request.user)
    messages.success(request, "ยืนยันคำให้การแล้ว")
    return back_to(case, TAB_OF[st.party.role])


@login_required
@require_POST
def rights(request, pk, party_id):
    case = editable(request, pk)
    cp = get_object_or_404(CaseParty.objects.select_related("person"), pk=party_id, case=case, role="suspect")
    plea = request.POST.get("plea")
    if request.POST.get("informed") != "on" or plea not in services.PLEAS:
        messages.error(request, "ติ๊ก 'แจ้งสิทธิแล้ว' และเลือกแนวคำให้การก่อน")
        return redirect(f"{case.get_absolute_url()}#suspects")
    at = parse_datetime(request.POST.get("informed_at", "")) or timezone.now()
    if timezone.is_naive(at):
        at = timezone.make_aware(at)
    try:
        services.inform_rights(cp, request.user, at, plea, draft=request.POST.get("draft") == "on")
        messages.success(request, f"บันทึกการแจ้งสิทธิแล้ว · ให้การ{services.PLEAS[plea]}")
    except AIError as e:
        messages.warning(request, f"บันทึกการแจ้งสิทธิแล้ว แต่ AI ร่างคำให้การไม่ได้: {e}")
    return redirect(f"{case.get_absolute_url()}#suspects")


@login_required
@require_POST
def consistency(request, pk, party_id):
    case = editable(request, pk)
    cp = get_object_or_404(CaseParty.objects.select_related("person"), pk=party_id, case=case)
    try:
        flags = services.check_consistency(cp, request.user)
    except AIError as e:
        return ai_error(request, e)
    return render(request, "ai/_flags.html", {"flags": flags, "has_statement": bool(cp.current_statement)})
