from urllib.parse import quote

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from cases.views import can_edit, get_case
from core.files import is_first_request, ranged

from . import services
from .models import CustodyEvent, EvidenceFile, Exhibit

DT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ExhibitForm(forms.ModelForm):
    class Meta:
        model = Exhibit
        fields = [
            "name",
            "quantity",
            "unit",
            "condition",
            "source",
            "party",
            "seized_at",
            "storage",
            "status",
            "holder",
            "is_digital",
            "note",
        ]
        widgets = {"seized_at": DT}

    def __init__(self, *a, case=None, **kw):
        super().__init__(*a, **kw)
        self.fields["seized_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["party"].queryset = case.parties.select_related("person") if case else self.fields["party"].queryset.none()
        self.fields["party"].label_from_instance = lambda cp: f"{cp.get_role_display()} {cp.person.full_name}"
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "input")


def case_for(request, pk, edit=False):
    case = get_case(request.user, pk)
    if edit and not can_edit(request.user, case):
        raise PermissionDenied
    return case


def back(case, tab="files"):
    return redirect(f"{reverse('evidence:case', args=[case.pk])}#{tab}")


@login_required
def case_page(request, pk):
    case = case_for(request, pk)
    files = list(case.evidence_files.select_related("exhibit", "uploaded_by"))
    for f in files:
        f.previewable = services.can_preview(f)
    exhibits = list(case.exhibits.select_related("party__person"))
    ctx = {
        "case": case, "editable": can_edit(request.user, case), "files": files, "exhibits": exhibits,
        "active_files": [f for f in files if not f.withdrawn_at], "events": case.custody_events.select_related("by", "evidence", "exhibit")[:200],
        "exhibit_form": ExhibitForm(case=case), "edit_forms": {x.pk: ExhibitForm(instance=x, case=case, prefix=f"x{x.pk}") for x in exhibits},
        "statuses": Exhibit.Status.choices, "active": "cases",
    }  # fmt: skip
    from audit.services import log

    log(request.user, "view", "evidence", case.pk, f"เปิดหน้าพยานหลักฐานและของกลาง คดี {case}")
    return render(request, "evidence/case.html", ctx)


@login_required
@require_POST
def upload(request, pk):
    case = case_for(request, pk, edit=True)
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "เลือกไฟล์อย่างน้อย 1 ไฟล์")
        return back(case)
    exhibit = case.exhibits.filter(pk=request.POST.get("exhibit") or 0).first()
    collected = None
    if request.POST.get("collected_at"):
        try:
            collected = forms.DateTimeField(input_formats=["%Y-%m-%dT%H:%M"]).clean(request.POST["collected_at"])
        except ValidationError:
            collected = None
    title = request.POST.get("title", "").strip()
    done = 0
    for i, f in enumerate(files, start=1):
        try:
            services.receive(case, request.user, f, f"{title} ({i})" if title and len(files) > 1 else title, request.POST.get("source", "").strip(),
                             collected, request.POST.get("is_digital") == "on", exhibit, request.POST.get("note", "").strip())  # fmt: skip
            done += 1
        except ValidationError as e:
            messages.error(request, f"{f.name}: {' '.join(e.messages)}")
    if done:
        messages.success(request, f"รับเข้า {done} ไฟล์ คำนวณ SHA-256 และล็อกไฟล์ต้นฉบับแล้ว")
    return back(case)


@login_required
def file_panel(request, pk, fid):
    case = case_for(request, pk)
    ev = get_object_or_404(EvidenceFile, pk=fid, case=case)
    ev.previewable = services.can_preview(ev)
    return render(
        request,
        "evidence/_file.html",
        {"case": case, "ev": ev, "events": ev.events.select_related("by")[:30], "editable": can_edit(request.user, case)},
    )


@login_required
def file_view(request, pk, fid):
    """Inline preview (no download). Logged once per opening (not for every byte-range request of a video)."""
    case = case_for(request, pk)
    ev = get_object_or_404(EvidenceFile, pk=fid, case=case)
    if not services.can_preview(ev):
        raise Http404
    if is_first_request(request):
        services.event(case, request.user, CustodyEvent.Action.VIEW, "เปิดดูในระบบ", evidence=ev)
    resp = ranged(request, ev.file.path, ev.content_type, ev.original_name)
    resp["X-Frame-Options"] = "SAMEORIGIN"  # the PDF viewer sits in a frame on our own page
    resp["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    )
    return resp


@login_required
@require_POST
def file_download(request, pk, fid):
    case = case_for(request, pk)
    ev = get_object_or_404(EvidenceFile, pk=fid, case=case)
    reason = request.POST.get("reason", "").strip()
    if len(reason) < 5:
        messages.error(request, "ระบุเหตุผลที่ดาวน์โหลด (อย่างน้อย 5 ตัวอักษร)")
        return back(case)
    services.event(case, request.user, CustodyEvent.Action.DOWNLOAD, reason, evidence=ev)
    resp = FileResponse(ev.file.open("rb"), content_type=ev.content_type)
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(ev.original_name)}"
    return resp


@login_required
@require_POST
def file_verify(request, pk, fid):
    case = case_for(request, pk)
    ev = get_object_or_404(EvidenceFile, pk=fid, case=case)
    if services.verify(ev, request.user):
        messages.success(request, f"{ev.title}: SHA-256 ตรงกับตอนรับเข้า ไฟล์ไม่ถูกแก้ไข")
    else:
        messages.error(request, f"{ev.title}: SHA-256 ไม่ตรงหรือไม่พบไฟล์ แจ้งหัวหน้างานสอบสวนแล้ว")
    return back(case)


@login_required
@require_POST
def file_withdraw(request, pk, fid):
    case = case_for(request, pk, edit=True)
    ev = get_object_or_404(EvidenceFile, pk=fid, case=case)
    try:
        services.withdraw(ev, request.user, request.POST.get("reason", ""))
        messages.success(request, f"ถอน {ev.title} ออกจากสำนวนแล้ว (ไฟล์และประวัติยังเก็บไว้)")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return back(case)


@login_required
@require_POST
def exhibit_add(request, pk):
    case = case_for(request, pk, edit=True)
    form = ExhibitForm(request.POST, case=case)
    if form.is_valid():
        x = services.add_exhibit(case, request.user, **form.cleaned_data)
        messages.success(request, f"เพิ่มของกลาง {x.order}. {x.name}")
    else:
        messages.error(request, " ".join(f"{form.fields[k].label}: {' '.join(v)}" for k, v in form.errors.items()))
    return back(case, "exhibits")


@login_required
@require_POST
def exhibit_update(request, pk, xid):
    case = case_for(request, pk, edit=True)
    x = get_object_or_404(Exhibit, pk=xid, case=case)
    form = ExhibitForm(request.POST, instance=Exhibit.objects.get(pk=x.pk), case=case, prefix=f"x{x.pk}")
    if form.is_valid():
        try:
            changed = services.update_exhibit(x, request.user, form.cleaned_data, request.POST.get("reason", ""))
            messages.success(request, f"บันทึกของกลาง {x.name}" + (f" (เปลี่ยน{', '.join(changed)})" if changed else ""))
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    else:
        messages.error(request, " ".join(f"{form.fields[k].label}: {' '.join(v)}" for k, v in form.errors.items()))
    return back(case, "exhibits")


@login_required
@require_POST
def exhibit_handover(request, pk, xid):
    case = case_for(request, pk, edit=True)
    x = get_object_or_404(Exhibit, pk=xid, case=case)
    try:
        if request.POST.get("direction") == "back":
            services.take_back(x, request.user, request.POST.get("to_whom", ""), request.POST.get("reason", ""))
            messages.success(request, f"รับคืน {x.name} แล้ว")
        else:
            services.handover(
                x, request.user, request.POST.get("to_whom", ""), request.POST.get("reason", ""), request.POST.get("status", "")
            )
            messages.success(request, f"บันทึกการส่งมอบ {x.name} แล้ว")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return back(case, "exhibits")
