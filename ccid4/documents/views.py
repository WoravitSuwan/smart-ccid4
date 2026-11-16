from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
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
