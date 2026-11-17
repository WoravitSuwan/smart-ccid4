from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from audit.services import log
from cases.views import can_edit, get_case
from core.convert import soffice_available

from . import services
from .models import DocumentJob, IssuedDocument


def may_issue(user, case) -> bool:
    return user.can("document.generate") and (can_edit(user, case) or user.role == "clerk")


@login_required
def documents_page(request, pk):
    case = get_case(request.user, pk)
    groups, states = services.states(case)
    by_group = [(g, [s for s in states if s.doc.group == g]) for g in groups]
    ctx = {
        "case": case,
        "by_group": by_group,
        "ready_count": sum(1 for s in states if s.ready),
        "total": len(states),
        "formats": services.FORMATS,
        "history": case.issued_documents.select_related("created_by")[:60],
        "job": (
            case.document_jobs.filter(created_by=request.user, pk=request.GET.get("job")).first()
            if (request.GET.get("job") or "").isdigit()
            else None
        ),
        "may_issue": may_issue(request.user, case),
        "pdf_available": soffice_available(),
        "active": "cases",
    }
    return render(request, "documents/page.html", ctx)


@login_required
@require_POST
def issue(request, pk):
    case = get_case(request.user, pk)
    if not may_issue(request.user, case):
        raise PermissionDenied
    keys = request.POST.getlist("doc")
    selections = [{"key": k, "items": [int(i) for i in request.POST.getlist(f"items-{k}") if i.isdigit()]} for k in keys]
    if not selections:
        messages.error(request, "เลือกเอกสารอย่างน้อย 1 รายการ")
        return redirect("documents:page", pk=case.pk)
    job = services.start_job(case, request.user, selections, request.POST.get("format", "docx"))
    return redirect(job_url(case, job))


def job_url(case, job):
    from django.urls import reverse

    return reverse("documents:page", args=[case.pk]) + f"?job={job.pk}#job"


@login_required
def job_status(request, pk, job_id):
    case = get_case(request.user, pk)
    job = get_object_or_404(DocumentJob, pk=job_id, case=case, created_by=request.user)
    return render(request, "documents/_job.html", {"case": case, "job": job, "files": services.bundle_files(job)})


def _send(request, case, f, what, obj_type, obj_id):
    if not f:
        raise Http404
    log(request.user, "download", obj_type, obj_id, f"ดาวน์โหลด{what} คดี {case}")
    return FileResponse(f.open("rb"), as_attachment=True, filename=f.name.rsplit("/", 1)[-1])


@login_required
def download(request, pk, doc_id, fmt):
    case = get_case(request.user, pk)
    d = get_object_or_404(IssuedDocument, pk=doc_id, case=case)
    return _send(request, case, d.pdf if fmt == "pdf" else d.docx, f" {d.title} ฉบับที่ {d.version} ({fmt})", "document", d.pk)


@login_required
def download_bundle(request, pk, job_id):
    case = get_case(request.user, pk)
    job = get_object_or_404(DocumentJob, pk=job_id, case=case)
    return _send(request, case, job.bundle, "ไฟล์รวมเอกสาร (ZIP)", "document_job", job.pk)


# ---------------------------------------------------------------- B12: รวมสำนวนเป็น PDF เล่มเดียว
@login_required
def bundle_page(request, pk):
    from . import bundle
    from .models import CaseBundle

    case = get_case(request.user, pk)
    if request.method == "POST":
        if not may_issue(request.user, case):
            raise PermissionDenied
        valid = {p.token for p in bundle.candidates(case)}
        picked = [t for t in request.POST.getlist("part") if t in valid]
        order = {t: request.POST.get(f"order-{t}", "") for t in picked}
        picked.sort(key=lambda t: (float(order[t]) if order[t].replace(".", "", 1).isdigit() else 9999))
        if not picked:
            messages.error(request, "เลือกอย่างน้อย 1 รายการ")
            return redirect("documents:case_bundle", pk=case.pk)
        b = CaseBundle.objects.create(case=case, request=picked, created_by=request.user)
        from .tasks import build_bundle

        build_bundle.delay(b.pk)
        return redirect(f"{reverse('documents:case_bundle', args=[case.pk])}?b={b.pk}")
    parts = bundle.candidates(case)
    current = case.bundles.filter(pk=request.GET.get("b") or 0).first()
    return render(request, "documents/bundle.html", {"case": case, "parts": parts, "current": current, "history": case.bundles.select_related("created_by")[:20],
                                                     "may_issue": may_issue(request.user, case), "pdf_available": soffice_available(), "active": "cases"})  # fmt: skip


@login_required
def bundle_status(request, pk, bid):
    case = get_case(request.user, pk)
    b = get_object_or_404(case.bundles, pk=bid)
    return render(request, "documents/_bundle_status.html", {"case": case, "b": b})


@login_required
def bundle_download(request, pk, bid):
    case = get_case(request.user, pk)
    b = get_object_or_404(case.bundles, pk=bid, status="done")
    log(request.user, "download", "case_bundle", b.pk, f"ดาวน์โหลดสำนวนรวม PDF {b.pages} หน้า คดี {case}")
    return FileResponse(b.file.open("rb"), as_attachment=True, filename=b.file.name.rsplit("/", 1)[-1])
