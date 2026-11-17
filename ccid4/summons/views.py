import io
from urllib.parse import quote

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import StyledMixin
from audit.services import log

from . import importer, services
from .models import (
    Complainant,
    ImportBatch,
    RegisterInvestigator,
    RegisterStatus,
    RegisterUnit,
    Summons,
    SummonsBatch,
    SummonsTemplate,
)

SESSION_KEY = "summons:selected"


def can_import(user) -> bool:
    return user.role in ("clerk", "supervisor", "admin")


def can_configure(user) -> bool:
    return user.role in ("supervisor", "admin")


def need(ok: bool):
    if not ok:
        raise PermissionDenied


@login_required
def index(request):
    ctx = services.dashboard(request.user) | {
        "can_import": can_import(request.user),
        "batches": SummonsBatch.objects.filter(created_by=request.user)[:8],
        "active": "summons",
    }
    return render(request, "summons/index.html", ctx)


# ---------------------------------------------------------------- import wizard
@login_required
def import_page(request):
    need(can_import(request.user))
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f or not f.name.lower().endswith((".xlsx", ".xlsm")):
            messages.error(request, "เลือกไฟล์ Excel (.xlsx)")
            return redirect("summons:import")
        batch = ImportBatch(created_by=request.user, file_name=f.name[:255], kind="")
        batch.file.save(f.name, f, save=False)
        try:
            book = importer.open_book(batch.file.path)
        except Exception:  # noqa: BLE001 - not an Excel file
            messages.error(request, "เปิดไฟล์ไม่ได้ ตรวจว่าเป็น Excel (.xlsx)")
            return redirect("summons:import")
        batch.kind = request.POST.get("kind") or importer.detect_kind(book) or ""
        if batch.kind not in ImportBatch.Kind.values:
            messages.error(request, "ไม่รู้ว่าเป็นไฟล์ผู้แจ้งหรือทะเบียนคดี เลือกชนิดไฟล์เองแล้วอัปโหลดใหม่")
            return redirect("summons:import")
        batch.sheets = [ws.title for ws in book.worksheets]
        batch.mapping = importer.auto_mapping(batch.kind, importer.headers_of(book.worksheets[0]))
        batch.save()
        return redirect("summons:import_review", pk=batch.pk)
    history = ImportBatch.objects.select_related("created_by")[:30]
    return render(request, "summons/import.html", {"history": history, "kinds": ImportBatch.Kind.choices, "active": "summons"})


@login_required
def import_review(request, pk):
    need(can_import(request.user))
    batch = get_object_or_404(ImportBatch, pk=pk)
    book = importer.open_book(batch.file.path)
    all_sheets = [ws.title for ws in book.worksheets]
    headers = importer.headers_of(book.worksheets[0])
    fields = importer.COLUMNS[batch.kind]
    if request.method == "POST" and batch.status == ImportBatch.Status.PREVIEW:
        batch.sheets = [s for s in request.POST.getlist("sheets") if s in all_sheets] or all_sheets
        mapping = {}
        for h in headers:
            target = request.POST.get(f"map:{h}", "")
            if target in fields.values():
                mapping[h] = target
        batch.mapping = mapping
        batch.save()
        custom = {h: f for h, f in mapping.items() if fields.get(h) != f}
        if custom:
            from .models import ColumnMapping

            ColumnMapping.objects.update_or_create(kind=batch.kind, defaults={"mapping": custom})
        if request.POST.get("action") == "apply":
            if importer.missing_fields(batch.kind, batch.mapping):
                messages.error(request, "ยังจับคู่คอลัมน์ที่จำเป็นไม่ครบ")
            else:
                c = importer.apply(batch, request.user)
                ch = " · ".join(f"{k} {v} คดี" for k, v in c.get("changes", {}).items())
                messages.success(
                    request, f"นำเข้าแล้ว: ใหม่ {c['new']} · อัปเดต {c['updated']} · มีปัญหา {c['issues']}{' · ' + ch if ch else ''}"
                )
                return redirect("summons:import_review", pk=batch.pk)
    analysis = importer.analyze(batch) if batch.status == ImportBatch.Status.PREVIEW else None
    ctx = {
        "batch": batch, "all_sheets": all_sheets, "headers": headers, "field_choices": sorted(set(fields.items()), key=lambda x: x[0]),
        "missing": importer.missing_fields(batch.kind, batch.mapping), "counts": analysis.counts() if analysis else batch.counts,
        "issues": (analysis.issues[:200] if analysis else [(i.sheet, i.row_no, i.receipt_no, i.problem) for i in batch.issues.all()[:200]]),
        "active": "summons",
    }  # fmt: skip
    return render(request, "summons/import_review.html", ctx)


@login_required
def import_issues_xlsx(request, pk):
    need(can_import(request.user))
    batch = get_object_or_404(ImportBatch, pk=pk)
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ปัญหา"
    ws.append(["ชีต", "แถว", "เลขรับแจ้ง", "ปัญหา"])
    issues = batch.issues.all() if batch.status == ImportBatch.Status.DONE else []
    rows = [(i.sheet, i.row_no, i.receipt_no, i.problem) for i in issues] or importer.analyze(batch).issues
    for r in rows:
        ws.append(list(r))
    return xlsx_response(wb, f"ปัญหาการนำเข้า-{batch.pk}.xlsx")


def xlsx_response(wb, name):
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return resp


# ---------------------------------------------------------------- register
def filtered(request):
    qs = services.visible(request.user).annotate(n_summons=Count("summons", filter=~Q(summons__result="cancelled")))
    g = request.GET
    if g.get("q"):
        q = g["q"].strip()
        qs = qs.filter(
            Q(receipt_no__icontains=q)
            | Q(case_no__icontains=q)
            | Q(victim_name__icontains=q)
            | Q(complainant__phone__icontains=q)
        )
    if g.get("open"):
        qs = qs.exclude(status__is_final=True)
    if g.get("bad_address"):
        qs = qs.filter(Q(complainant__isnull=True) | Q(complainant__house_no="") | Q(complainant__province=""))
    if g.get("never"):
        qs = qs.filter(summons__isnull=True)
    for key, lookup in (("status", "status_id"), ("unit", "unit_id"), ("investigator", "investigator_id")):
        if g.get(key, "").isdigit():
            qs = qs.filter(**{lookup: int(g[key])})
    if g.get("year", "").isdigit():
        qs = qs.filter(report_date__year=int(g["year"]) - 543)
    if g.get("type"):
        qs = qs.filter(case_type=g["type"])
    return qs.distinct()


@login_required
def register(request):
    qs = filtered(request)
    page = Paginator(qs.order_by("-report_date", "receipt_no"), 50).get_page(request.GET.get("page"))
    years = sorted(
        {d.year + 543 for d in services.visible(request.user).exclude(report_date=None).values_list("report_date", flat=True)},
        reverse=True,
    )
    ctx = {
        "page": page, "total": qs.count(), "params": request.GET, "query": request.GET.urlencode(),
        "statuses": RegisterStatus.objects.all(), "units": RegisterUnit.objects.all(), "investigators": RegisterInvestigator.objects.all(),
        "types": services.visible(request.user).exclude(case_type="").values_list("case_type", flat=True).distinct().order_by("case_type"),
        "years": years, "can_issue": services.can_issue(request.user), "active": "summons",
    }  # fmt: skip
    if request.headers.get("HX-Request"):
        return render(request, "summons/_register_table.html", ctx)
    return render(request, "summons/register.html", ctx)


EXPORT_COLUMNS = list(importer.REGISTER_COLUMNS.keys())


@login_required
def register_xlsx(request):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ทะเบียนคดี"
    ws.append(EXPORT_COLUMNS)
    qs = filtered(request).select_related("status", "unit", "investigator")
    for rc in qs:
        row = []
        for header in EXPORT_COLUMNS:
            f = importer.REGISTER_COLUMNS[header]
            if f == "_day":
                row.append(rc.report_date.day if rc.report_date else "")
            elif f == "_month":
                row.append(rc.report_date.month if rc.report_date else "")
            elif f == "_year":
                row.append(rc.report_date.year if rc.report_date else "")
            elif f == "_unit":
                row.append(rc.unit.name if rc.unit else "")
            elif f == "_investigator":
                row.append(rc.investigator.name if rc.investigator else "")
            elif f == "_status":
                row.append(rc.status.name if rc.status else "")
            elif f == "damage":
                row.append(float(rc.damage))
            else:
                row.append(getattr(rc, f, ""))
        ws.append(row)
    log(request.user, "export", "register", "", f"ส่งออกทะเบียนคดีเป็น Excel {qs.count()} แถว")
    return xlsx_response(wb, f"ทะเบียนคดี-{timezone.localdate():%Y%m%d}.xlsx")


class AddressForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Complainant
        fields = [
            "title",
            "first_name",
            "last_name",
            "phone",
            "house_no",
            "moo",
            "building",
            "soi",
            "road",
            "tambon",
            "amphoe",
            "province",
            "postcode",
        ]


@login_required
def register_detail(request, pk):
    rc = get_object_or_404(services.visible(request.user), pk=pk)
    c = rc.complainant
    form = AddressForm(request.POST or None, instance=c) if c else None
    if request.method == "POST" and form is not None:
        need(services.can_issue(request.user, rc) or can_import(request.user))
        if form.is_valid():
            services.update_address(request.user, c, {k: form.cleaned_data[k] for k in form.changed_data})
            messages.success(request, "บันทึกที่อยู่แล้ว")
            return redirect("summons:detail", pk=rc.pk)
    log(request.user, "view", "register_case", rc.pk, f"เปิดทะเบียนคดี {rc.receipt_no}")
    ctx = {"rc": rc, "c": c, "form": form, "summons": rc.summons.select_related("issuer", "created_by"), "results": Summons.Result.choices,
           "can_issue": services.can_issue(request.user, rc), "can_reveal": request.user.can("pii.reveal"), "active": "summons"}  # fmt: skip
    return render(request, "summons/detail.html", ctx)


@login_required
@require_POST
def reveal_id(request, pk):
    rc = get_object_or_404(services.visible(request.user), pk=pk)
    need(request.user.can("pii.reveal") and rc.complainant)
    log(request.user, "reveal", "complainant", rc.complainant.pk, f"ดูเลขบัตรผู้แจ้งเต็ม เลขรับแจ้ง {rc.receipt_no}")
    return HttpResponse(rc.complainant.citizen_id or "-")


@login_required
@require_POST
def set_result(request, pk):
    s = get_object_or_404(Summons.objects.select_related("register_case"), pk=pk)
    rc = get_object_or_404(services.visible(request.user), pk=s.register_case_id)
    need(services.can_issue(request.user, rc))
    if request.POST.get("result") in Summons.Result.values:
        s.result = request.POST["result"]
        s.save(update_fields=["result"])
        log(request.user, "edit", "summons", s.pk, f"ผลหมายเรียก {rc.receipt_no} ครั้งที่ {s.number}: {s.get_result_display()}")
    return redirect("summons:detail", pk=rc.pk)


# ---------------------------------------------------------------- issue
class IssueForm(StyledMixin, forms.Form):
    issued_on = forms.DateField(label="วันออกหมาย", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    appointment_at = forms.DateTimeField(
        label="วันเวลานัด (เว้นว่าง = ออกหมาย + 14 วัน 09.00 น. เลี่ยงเสาร์–อาทิตย์)", required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )  # fmt: skip
    stagger = forms.BooleanField(label="ไล่เวลานัดทีละ 30 นาที (แยกตามพนักงานสอบสวน)", required=False)
    place = forms.CharField(label="สถานที่นัด (เว้นว่าง = ตามหน่วยที่รับผิดชอบ)", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    reason = forms.CharField(label="เหตุที่เรียก", initial=services.DEFAULT_REASON, max_length=400)
    thai_digits = forms.BooleanField(label="แปลงตัวเลขในข้อมูลเป็นเลขไทย", required=False, initial=True)
    set_status = forms.BooleanField(label="เปลี่ยนสถานะคดีเป็น 'ออกหมายเรียก'", required=False)
    formats = forms.ChoiceField(label="ไฟล์", choices=[("both", "Word และ PDF"), ("pdf", "PDF"), ("docx", "Word")], initial="both")
    combine = forms.BooleanField(label="รวมเป็นไฟล์เดียว (ไม่ติ๊ก = แยกรายคนใส่ ZIP)", required=False)
    envelopes = forms.BooleanField(label="พิมพ์ใบจ่าหน้าซองด้วย", required=False, initial=True)


def selected_cases(request):
    ids = [int(i) for i in request.session.get(SESSION_KEY, [])]
    return list(services.visible(request.user).filter(pk__in=ids))


@login_required
def issue(request):
    need(services.can_issue(request.user))
    if request.method == "POST" and request.POST.get("ids") is not None:
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        if request.POST.get("all_filtered"):
            ids = list(filtered(request).values_list("pk", flat=True))
        request.session[SESSION_KEY] = ids
        return redirect("summons:issue")
    cases = [rc for rc in selected_cases(request) if services.can_issue(request.user, rc)]
    if not cases:
        messages.error(request, "เลือกคดีจากทะเบียนก่อน")
        return redirect("summons:register")
    form = IssueForm(request.POST or None, initial={"issued_on": timezone.localdate()})
    opts = {"issued_on": timezone.localdate(), "thai_digits": True, "reason": services.DEFAULT_REASON}
    if form.is_bound and form.is_valid():
        opts = form.cleaned_data
    items = services.plan(cases, opts)
    blocked = [i for i in items if i.problems]
    if request.method == "POST" and form.is_valid() and request.POST.get("action") == "confirm":
        ok_ids = [i.rc.pk for i in items if not i.problems]
        if not ok_ids:
            messages.error(request, "ไม่มีหมายที่ออกได้ แก้ปัญหาที่แสดงก่อน")
        else:
            batch = services.start_batch(request.user, ok_ids, opts)
            request.session.pop(SESSION_KEY, None)
            return redirect("summons:batch", pk=batch.pk)
    groups = {}
    for i in items:
        groups.setdefault(i.rc.investigator, []).append(i)
    ctx = {"form": form, "groups": list(groups.items()), "count": len(items), "blocked": blocked, "ready": len(items) - len(blocked),
           "template": services.active_template(SummonsTemplate.Kind.SUMMONS), "active": "summons"}  # fmt: skip
    return render(request, "summons/issue.html", ctx)


@login_required
def issue_preview(request):
    """ตัวอย่างหมายฉบับแรกเป็น PDF ก่อนยืนยัน."""
    need(services.can_issue(request.user))
    cases = [rc for rc in selected_cases(request) if services.can_issue(request.user, rc)]
    if not cases:
        raise Http404
    form = IssueForm(request.GET or None, initial={"issued_on": timezone.localdate()})
    opts = form.cleaned_data if form.is_bound and form.is_valid() else {"issued_on": timezone.localdate(), "thai_digits": True}
    item = next((i for i in services.plan(cases, opts) if not i.problems), None)
    if item is None:
        raise Http404
    data, ctype = services.preview_pdf(item, opts.get("thai_digits", True))
    resp = HttpResponse(data, content_type=ctype)
    resp["Content-Disposition"] = (
        "inline; filename=preview.pdf" if ctype == "application/pdf" else "attachment; filename=preview.docx"
    )
    resp["Content-Security-Policy"] = "frame-ancestors 'self'"
    resp["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@login_required
def batch_page(request, pk):
    batch = get_object_or_404(SummonsBatch, pk=pk, created_by=request.user)
    tpl = "summons/_batch.html" if request.headers.get("HX-Request") else "summons/batch.html"
    return render(
        request, tpl, {"batch": batch, "summons": batch.summons.select_related("register_case")[:200], "active": "summons"}
    )


@login_required
def download(request, kind, pk):
    if kind in ("bundle", "combined_pdf", "combined_docx", "envelopes"):
        obj = get_object_or_404(SummonsBatch, pk=pk, created_by=request.user)
        f = getattr(obj, kind)
        label = f"ไฟล์รวมหมายเรียก ({kind})"
    elif kind in ("docx", "pdf", "envelope"):
        obj = get_object_or_404(Summons.objects.select_related("register_case"), pk=pk)
        get_object_or_404(services.visible(request.user), pk=obj.register_case_id)
        f = getattr(obj, kind)
        label = f"หมายเรียก {obj.register_case.receipt_no} ครั้งที่ {obj.number} ({kind})"
    else:
        raise Http404
    if not f:
        raise Http404
    log(request.user, "download", "summons", obj.pk, f"ดาวน์โหลด{label}")
    return FileResponse(f.open("rb"), as_attachment=True, filename=f.name.rsplit("/", 1)[-1])


# ---------------------------------------------------------------- settings
InvestigatorFormSet = forms.modelformset_factory(
    RegisterInvestigator, fields=["rank_full", "sign_name", "position", "phone", "unit", "user"], extra=0,
    widgets={f: forms.TextInput(attrs={"class": "input"}) for f in ["rank_full", "sign_name", "position", "phone"]},
)  # fmt: skip
UnitFormSet = forms.modelformset_factory(
    RegisterUnit, fields=["issue_place", "appointment_place", "sender_address"], extra=0,
    widgets={"issue_place": forms.TextInput(attrs={"class": "input"}), "appointment_place": forms.Textarea(attrs={"rows": 2, "class": "input"}),
             "sender_address": forms.Textarea(attrs={"rows": 2, "class": "input"})},
)  # fmt: skip
StatusFormSet = forms.modelformset_factory(RegisterStatus, fields=["is_final"], extra=0)


@login_required
def settings_page(request):
    need(can_configure(request.user) or can_import(request.user))
    data = request.POST if request.method == "POST" else None
    inv_fs = InvestigatorFormSet(data, prefix="inv", queryset=RegisterInvestigator.objects.all())
    unit_fs = UnitFormSet(data, prefix="unit", queryset=RegisterUnit.objects.all())
    status_fs = StatusFormSet(data, prefix="st", queryset=RegisterStatus.objects.all())
    for f in inv_fs.forms:
        for name in ("unit", "user"):
            f.fields[name].widget.attrs["class"] = "input"
    if request.method == "POST":
        section = request.POST.get("section")
        fs = {"inv": inv_fs, "unit": unit_fs, "status": status_fs}.get(section)
        if section == "template":
            need(can_configure(request.user))
            return upload_template(request)
        if fs is not None and fs.is_valid():
            if section == "status":
                need(can_configure(request.user))
            fs.save()
            log(request.user, "edit", "summons_settings", section, f"แก้ตั้งค่าหมายเรียก ({section})")
            messages.success(request, "บันทึกแล้ว")
            return redirect(f"{request.path}#{section}")
        messages.error(request, "บันทึกไม่ได้ ตรวจข้อมูลอีกครั้ง")
    services.ensure_templates()
    ctx = {"inv_fs": inv_fs, "unit_fs": unit_fs, "status_fs": status_fs, "templates": SummonsTemplate.objects.all(),
           "can_configure": can_configure(request.user), "active": "summons"}  # fmt: skip
    return render(request, "summons/settings.html", ctx)


def upload_template(request):
    from django.core.files.base import ContentFile
    from django.db.models import Max

    from documents.engine import find_placeholders

    f = request.FILES.get("file")
    kind = request.POST.get("kind")
    if not f or kind not in SummonsTemplate.Kind.values or not f.name.lower().endswith(".docx"):
        messages.error(request, "เลือกไฟล์ .docx และชนิดแบบฟอร์ม")
        return redirect(f"{request.path}#template")
    data = f.read()
    try:
        names = find_placeholders(data)
    except Exception:  # noqa: BLE001 - not a valid docx
        messages.error(request, "เปิดไฟล์ไม่ได้")
        return redirect(f"{request.path}#template")
    version = (SummonsTemplate.objects.filter(kind=kind).aggregate(m=Max("version"))["m"] or 0) + 1
    t = SummonsTemplate(
        kind=kind, name=request.POST.get("name") or f.name, version=version, placeholders=names, created_by=request.user
    )
    t.file.save(f"{kind}-v{version}.docx", ContentFile(data), save=False)
    t.save()
    if request.POST.get("activate"):
        SummonsTemplate.objects.filter(kind=kind).exclude(pk=t.pk).update(active=False)
        t.active = True
        t.save(update_fields=["active"])
    log(request.user, "create", "summons_template", t.pk, f"อัปโหลดแบบฟอร์ม{t.get_kind_display()} ฉบับที่ {version}")
    messages.success(request, f"อัปโหลดแล้ว พบตัวแทน {len(names)} ช่อง: " + ", ".join(names))
    return redirect(f"{request.path}#template")


@login_required
@require_POST
def activate_template(request, pk):
    need(can_configure(request.user))
    t = get_object_or_404(SummonsTemplate, pk=pk)
    SummonsTemplate.objects.filter(kind=t.kind).update(active=False)
    t.active = True
    t.save(update_fields=["active"])
    log(request.user, "edit", "summons_template", t.pk, f"ใช้แบบฟอร์ม{t.get_kind_display()} ฉบับที่ {t.version}")
    return redirect("summons:settings")
