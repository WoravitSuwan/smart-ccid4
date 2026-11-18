import io

import segno
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.forms import formset_factory
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import StyledMixin
from ai.llm import AIError
from audit.services import log

from . import public_views, services
from .models import ArrestImport, IntakeKind, IntakeSubmission


def can_take(user):
    if not user.can("case.edit"):
        raise PermissionDenied


def qr_svg(url: str) -> str:
    return segno.make(url, error="m").svg_inline(scale=6, dark="#0E1A2E", light="#FFFFFF", border=2)


@login_required
def index(request):
    can_take(request.user)
    link = services.link_for(request.user)
    urls = {k: services.public_url(request, link, k) for k in IntakeKind.values}
    subs = IntakeSubmission.objects.filter(link__owner=request.user).select_related("case").prefetch_related("files")[:50]
    ctx = {
        "link": link,
        "start_url": services.public_url(request, link),
        "cards": [(k, label, urls[k], qr_svg(urls[k])) for k, label in IntakeKind.choices],
        "subs": subs,
        "imports": ArrestImport.objects.filter(created_by=request.user)[:20],
        "sources": ArrestImport.Source.choices,
        "active": "intake",
    }
    return render(request, "intake/index.html", ctx)


@login_required
def qr_png(request, kind):
    can_take(request.user)
    url = services.public_url(request, services.link_for(request.user), public_views.kind_or_404(kind))
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=12, border=3, dark="#0E1A2E")
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Content-Disposition"] = f'attachment; filename="ccid4-qr-{kind}.png"'
    return resp


@login_required
def poster(request, kind):
    can_take(request.user)
    kind = public_views.kind_or_404(kind)
    url = services.public_url(request, services.link_for(request.user), kind)
    return render(
        request,
        "intake/poster.html",
        {"kind_label": IntakeKind(kind).label, "url": url, "qr": qr_svg(url), "officer": request.user},
    )


@login_required
@require_POST
def regenerate(request):
    can_take(request.user)
    services.regenerate_link(request.user)
    messages.success(request, "สร้างลิงก์ใหม่แล้ว ลิงก์และ QR เดิมใช้ไม่ได้แล้ว (ป้ายที่พิมพ์ไว้ต้องพิมพ์ใหม่)")
    return redirect("intake:index")


@login_required
def staff_form(request, kind):
    """กรอกแทนผู้แจ้ง (A2.1): the same steps as the public form, filled in by the officer."""
    can_take(request.user)
    link = services.link_for(request.user)
    return public_views.step_form(request, link.token, kind, staff=request.user, link=link)


@login_required
@require_POST
def retry(request, sub_id):
    can_take(request.user)
    sub = get_object_or_404(IntakeSubmission, pk=sub_id, link__owner=request.user)
    services.process_submission(sub)
    if sub.case_id:
        messages.success(request, f"ตั้งเป็นคดีแล้ว ({sub.reference})")
    else:
        messages.error(request, f"ยังไม่สำเร็จ: {sub.error}")
    return redirect("intake:index")


@login_required
def intake_file(request, file_id):
    from .models import IntakeFile

    f = get_object_or_404(IntakeFile.objects.select_related("submission__link", "submission__case"), pk=file_id)
    sub = f.submission
    if not (
        sub.link.owner_id == request.user.pk
        or (sub.case and sub.case.__class__.objects.visible_to(request.user).filter(pk=sub.case_id).exists())
    ):
        raise PermissionDenied
    log(request.user, "download", "intake_file", f.pk, f"เปิดไฟล์แนบ {f.original_name} เลขอ้างอิง {sub.reference}")
    return FileResponse(f.file.open("rb"), filename=f.original_name, content_type=f.content_type)


# ---------------------------------------------------------------- import from arrest records
class SuspectForm(StyledMixin, forms.Form):
    name = forms.CharField(label="ชื่อ-นามสกุล (มีคำนำหน้า)", max_length=200)
    citizen_id = forms.CharField(label="เลขประจำตัวประชาชน", max_length=20, required=False)
    address = forms.CharField(label="ที่อยู่", max_length=400, required=False)
    marks = forms.CharField(label="ตำหนิรูปพรรณ", max_length=400, required=False)
    arrested_at = forms.DateTimeField(
        label="วันเวลาที่จับกุม", required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
    )


SuspectFormSet = formset_factory(SuspectForm, extra=0, min_num=0, can_delete=True)


class ImportForm(StyledMixin, forms.Form):
    title = forms.CharField(label="เรื่อง", max_length=300, required=False)
    offense = forms.CharField(label="ข้อหา", max_length=400)
    law_sections = forms.CharField(label="กฎหมายและมาตรา", max_length=400, required=False)
    incident_at = forms.DateTimeField(
        label="วันเวลาเกิดเหตุ/จับกุม",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    arrest_place = forms.CharField(label="สถานที่จับกุม", max_length=400, required=False)
    accuser = forms.CharField(label="ผู้กล่าวหา (หัวหน้าชุดจับกุมหรือผู้เสียหาย)", max_length=200, required=False)
    arrest_team = forms.CharField(label="เจ้าหน้าที่ผู้จับกุม (คั่นด้วยจุลภาค)", max_length=600, required=False)
    exhibits = forms.CharField(label="ของกลาง", max_length=600, required=False)
    circumstances = forms.CharField(label="พฤติการณ์การจับกุม", widget=forms.Textarea(attrs={"rows": 6}), required=False)


def import_initial(data: dict) -> tuple[dict, list[dict]]:
    from ai.fake import split_name  # noqa: F401 - same helper the review uses back

    def full(p):
        return f"{p.get('title', '')}{p.get('first_name', '')} {p.get('last_name', '')}".strip()

    case = {
        "title": data.get("title", ""), "offense": data.get("offense", ""), "law_sections": data.get("law_sections", ""),
        "incident_at": (data.get("incident_at") or "")[:16], "arrest_place": data.get("arrest_place", ""),
        "accuser": full(data["accuser"]) if data.get("accuser") else "", "arrest_team": ", ".join(data.get("arrest_team") or []),
        "exhibits": ", ".join(p["name"] for p in data.get("properties") or []), "circumstances": data.get("circumstances", ""),
    }  # fmt: skip
    suspects = [
        {"name": full(s), "citizen_id": s.get("citizen_id", ""), "address": s.get("address", ""), "marks": s.get("marks", ""),
         "arrested_at": (s.get("arrested_at") or "")[:16]}
        for s in data.get("suspects") or []
    ]  # fmt: skip
    return case, suspects


def reviewed_data(form, fs) -> dict:
    from ai.fake import split_name

    d = form.cleaned_data
    iso = lambda v: v.isoformat() if v else ""  # noqa: E731
    return {
        "title": d["title"] or f"จับกุมผู้ต้องหา ข้อหา {d['offense']}", "offense": d["offense"], "law_sections": d["law_sections"],
        "incident_at": iso(d["incident_at"]), "location": d["arrest_place"], "arrest_place": d["arrest_place"],
        "circumstances": d["circumstances"], "damage_amount": 0, "exhibits": d["exhibits"],
        "accuser": split_name(d["accuser"]) | {"occupation": "เจ้าพนักงานตำรวจ"} if d["accuser"] else None,
        "arrest_team": [x.strip() for x in d["arrest_team"].split(",") if x.strip()],
        "suspects": [split_name(f.cleaned_data["name"]) | {k: f.cleaned_data[k] for k in ("citizen_id", "address", "marks")}
                     | {"arrested_at": iso(f.cleaned_data["arrested_at"])}
                     for f in fs.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")],
        "witnesses": [], "properties": [], "accounts": [],
    }  # fmt: skip


@login_required
@require_POST
def import_upload(request):
    can_take(request.user)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "เลือกไฟล์ก่อน")
        return redirect("intake:index")
    source = (
        request.POST.get("source") if request.POST.get("source") in ArrestImport.Source.values else ArrestImport.Source.ARREST
    )
    try:
        imp = services.start_import(request.user, upload, source)
    except AIError as e:
        messages.error(request, str(e))
        return redirect("intake:index")
    if imp.status == ArrestImport.Status.FAILED:
        messages.warning(request, f"อ่านไฟล์อัตโนมัติไม่สำเร็จ: {imp.error} · กรอกข้อมูลเองในหน้าตรวจทานได้")
    return redirect("intake:import_review", pk=imp.pk)


@login_required
def import_review(request, pk):
    can_take(request.user)
    imp = get_object_or_404(ArrestImport, pk=pk, created_by=request.user)
    if imp.status == ArrestImport.Status.DONE and imp.case_id:
        return redirect(imp.case.get_absolute_url())
    case_init, suspects_init = import_initial(imp.extracted or {})
    form = ImportForm(request.POST or None, initial=case_init)
    fs = SuspectFormSet(request.POST or None, initial=suspects_init or [{}], prefix="s")
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        case = services.finish_import(imp, request.user, reviewed_data(form, fs))
        messages.success(request, f"สร้างคดีจาก{imp.get_source_display()}แล้ว ตรวจข้อมูลในแต่ละแท็บอีกครั้ง")
        return redirect(case.get_absolute_url())
    log(request.user, "view", "arrest_import", imp.pk, f"ตรวจทานไฟล์นำเข้า {imp.original_name}")
    return render(request, "intake/import_review.html", {"imp": imp, "form": form, "fs": fs, "active": "intake"})


@login_required
def import_file(request, pk):
    """The original upload, shown next to the review form (same-origin frame allowed only here)."""
    imp = get_object_or_404(ArrestImport, pk=pk, created_by=request.user)
    resp = FileResponse(imp.file.open("rb"), content_type=imp.content_type)
    # Only allow framing by our own pages; a stricter policy here can stop the browser's built-in PDF viewer.
    resp["Content-Security-Policy"] = "frame-ancestors 'self'"
    resp["X-Frame-Options"] = "SAMEORIGIN"
    return resp


# ---------------------------------------------------------------- B8: ลิงก์ติดตามสถานะของผู้แจ้ง (จากหน้าคดี)
def _tracking_case(request, pk):
    from cases.views import can_edit, get_case

    case = get_case(request.user, pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    return case


@login_required
@require_POST
def tracking_new(request, pk):
    from django.core.exceptions import ValidationError

    from . import tracking

    case = _tracking_case(request, pk)
    try:
        tracking.create(case, request.user, int(request.POST.get("days") or tracking.DEFAULT_DAYS))
        messages.success(request, "สร้างลิงก์ติดตามแล้ว ส่ง QR หรือลิงก์ให้ผู้แจ้ง (ลิงก์เดิมถูกยกเลิก)")
    except (ValidationError, ValueError) as e:
        messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
    return redirect(f"{case.get_absolute_url()}#investigator")


@login_required
@require_POST
def tracking_revoke(request, pk):
    from . import tracking

    case = _tracking_case(request, pk)
    t = tracking.current(case)
    if t:
        tracking.revoke(t, request.user)
        messages.success(request, "ยกเลิกลิงก์ติดตามแล้ว ผู้แจ้งเปิดลิงก์เดิมไม่ได้อีก")
    return redirect(f"{case.get_absolute_url()}#investigator")


@login_required
@require_POST
def tracking_message(request, pk):
    from django.core.exceptions import ValidationError

    from . import tracking

    case = _tracking_case(request, pk)
    try:
        tracking.publish_message(case, request.user, request.POST.get("text", ""))
        messages.success(request, "เผยแพร่ข้อความให้ผู้แจ้งเห็นแล้ว (บันทึกในบันทึกพนักงานสอบสวน)")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(f"{case.get_absolute_url()}#investigator")


@login_required
def tracking_qr(request, pk):
    from cases.views import get_case

    from . import tracking

    case = get_case(request.user, pk)
    t = tracking.current(case)
    if not t:
        raise PermissionDenied
    url = (
        request.build_absolute_uri(f"/t/{t.token}/")
        if not settings.PUBLIC_BASE_URL
        else f"{settings.PUBLIC_BASE_URL.rstrip('/')}/t/{t.token}/"
    )
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=3, dark="#0E1A2E")
    return HttpResponse(buf.getvalue(), content_type="image/png")
