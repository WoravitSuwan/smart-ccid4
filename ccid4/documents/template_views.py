from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cases.models import Case

from . import catalog
from . import templates_admin as T
from .models import CustomDocType, TemplateVersion


def manager(request):
    if request.user.role not in ("admin", "supervisor"):
        raise PermissionDenied


@login_required
def index(request):
    manager(request)
    groups, docs = catalog.load()
    active = {t.key: t for t in TemplateVersion.objects.filter(active=True)}
    counts = {}
    for k in TemplateVersion.objects.values_list("key", flat=True):
        counts[k] = counts.get(k, 0) + 1
    rows = [(g, [(d, active.get(d.key), counts.get(d.key, 0)) for d in docs.values() if d.group == g]) for g in groups]
    return render(request, "documents/templates/index.html", {"rows": rows, "groups": groups, "per_choices": CustomDocType.PER_CHOICES,
                                                              "active": "templates"})  # fmt: skip


@login_required
@require_POST
def add_custom(request):
    manager(request)
    try:
        c = T.add_custom(request.user, request.POST.get("title", ""), request.POST.get("group", ""), request.POST.get("per", ""))
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("doctemplates:index")
    messages.success(request, f"เพิ่มเอกสาร “{c.title}” แล้ว อัปโหลดไฟล์ Word ที่มี {{ช่อง}} ได้เลย")
    return redirect("doctemplates:manage", key=c.key)


@login_required
def manage(request, key):
    manager(request)
    doc = T.docs().get(key)
    if doc is None:
        raise PermissionDenied
    versions = TemplateVersion.objects.filter(key=key).select_related("uploaded_by")
    cases = Case.objects.visible_to(request.user).order_by("-updated_at")[:50]
    return render(request, "documents/templates/manage.html", {"doc": doc, "versions": versions, "builtin": doc.path.exists(),
                                                               "cases": cases, "active": "templates"})  # fmt: skip


@login_required
@require_POST
def upload(request, key):
    manager(request)
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "เลือกไฟล์ .docx")
        return redirect("doctemplates:manage", key=key)
    try:
        tv = T.upload(key, request.user, f, request.POST.get("note", ""))
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("doctemplates:manage", key=key)
    messages.success(request, f"อัปโหลดเวอร์ชัน {tv.version} แล้ว พบ {len(tv.placeholders)} ช่อง ตรวจการจับคู่แล้วกดเปิดใช้")
    return redirect("doctemplates:mapping", pk=tv.pk)


@login_required
def mapping(request, pk):
    manager(request)
    tv = get_object_or_404(TemplateVersion, pk=pk)
    doc = T.docs()[tv.key]
    known = T.system_fields()
    if request.method == "POST":
        chosen = {p: request.POST.get(f"map-{i}", "") for i, p in enumerate(tv.placeholders)}
        try:
            T.save_mapping(tv, request.user, chosen, request.POST.get("digits", ""), request.POST.get("activate") == "on")
            messages.success(request, "บันทึกการจับคู่แล้ว" + (" และเปิดใช้เวอร์ชันนี้" if request.POST.get("activate") == "on" else ""))
            return redirect("doctemplates:manage", key=tv.key)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    groups: dict[str, list] = {}
    for path, kind in known.items():
        groups.setdefault(path.split(".", 1)[0], []).append((path, kind))
    rows = [(i, p, tv.mapping.get(p, ""), p in known) for i, p in enumerate(tv.placeholders)]
    return render(
        request, "documents/templates/mapping.html", {"tv": tv, "doc": doc, "rows": rows, "groups": groups, "active": "templates"}
    )


@login_required
@require_POST
def activate(request, pk):
    manager(request)
    tv = get_object_or_404(TemplateVersion, pk=pk)
    T.activate(tv, request.user)
    messages.success(request, f"เปิดใช้เวอร์ชัน {tv.version} แล้ว เอกสารที่ออกต่อจากนี้จะอ้างอิง {tv.label}")
    return redirect("doctemplates:manage", key=tv.key)


@login_required
@require_POST
def builtin(request, key):
    manager(request)
    try:
        T.use_builtin(key, request.user)
        messages.success(request, "กลับไปใช้ไฟล์มาตรฐานของระบบแล้ว")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("doctemplates:manage", key=key)


@login_required
def download(request, key, pk=0):
    manager(request)
    if pk:
        tv = get_object_or_404(TemplateVersion, pk=pk, key=key)
        return FileResponse(tv.file.open("rb"), as_attachment=True, filename=f"{key}-v{tv.version}.docx")
    doc = T.docs()[key]
    if not doc.path.exists():
        raise PermissionDenied
    return FileResponse(open(doc.path, "rb"), as_attachment=True, filename=f"{key}-มาตรฐาน.docx")  # noqa: SIM115


@login_required
@require_POST
def try_fill(request, pk):
    manager(request)
    tv = get_object_or_404(TemplateVersion, pk=pk)
    case = Case.objects.visible_to(request.user).filter(pk=request.POST.get("case") or 0).first()
    if not case:
        messages.error(request, "เลือกคดีที่จะทดลอง")
        return redirect("doctemplates:manage", key=tv.key)
    data, missing = T.try_fill(tv, case, request.user)
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'ทดลอง-{tv.key}-v{tv.version}.docx')}"
    resp["X-Missing-Fields"] = str(len(missing))
    return resp
