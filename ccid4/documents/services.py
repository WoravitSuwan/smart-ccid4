"""Issue documents for a case: readiness, building files, PDF, ZIP, history and the investigator log."""

from __future__ import annotations

import unicodedata
import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.services import log
from core.convert import docx_to_pdf

from . import catalog, context, engine
from .models import DocumentJob, IssuedDocument

FORMATS = {"docx": "Word (.docx)", "pdf": "PDF", "both": "ทั้ง Word และ PDF"}


def candidates(case, doc: catalog.DocType) -> list:
    """People (or letters) a per-item document can be issued for."""
    if doc.per == "letter":
        return list(case.letters.all())
    roles = catalog.PER_ROLES.get(doc.per, [])
    return [p for p in case.parties.select_related("person").all() if p.role in roles]


def item_label(obj) -> str:
    if hasattr(obj, "person"):
        return f"{obj.get_role_display()} {obj.person.full_name}"
    return f"{obj.number or 'ร่าง'} {obj.subject}"


def states(case) -> tuple[list[str], list[catalog.DocState]]:
    groups, docs = catalog.load()
    base = context.build(case)
    cache: dict = {}
    out = []
    for doc in docs.values():
        item_values = {}
        for obj in candidates(case, doc) if doc.per else []:
            ck = (type(obj).__name__, obj.pk)
            if ck not in cache:
                kw = {"letter": obj} if doc.per == "letter" else {"party": obj}
                cache[ck] = context.build(case, **kw)
            item_values[obj.pk] = (item_label(obj), obj, cache[ck])
        out.append(catalog.state_for(case, doc, base, item_values))
    return groups, out


def start_job(case, user, selections: list[dict], formats: str) -> DocumentJob:
    """selections: [{"key": "...", "items": [ids]}]. The heavy work runs in Celery (documents.tasks)."""
    from .tasks import generate_job

    job = DocumentJob.objects.create(
        case=case, request=selections, formats=formats if formats in FORMATS else "docx", created_by=user
    )
    generate_job.delay(job.pk)
    return job


def _filename(doc: catalog.DocType, label: str, version: int, ext: str) -> str:
    safe = "".join(ch for ch in label if unicodedata.category(ch)[0] in "LMN" or ch in " -_").strip().replace(" ", "_")[:60]
    return f"{doc.no:02d}-{doc.key}{'-' + safe if safe else ''}-v{version}.{ext}"


def build_one(job: DocumentJob, doc: catalog.DocType, obj=None) -> IssuedDocument:
    case = job.case
    kw = {}
    if doc.per == "letter":
        kw["letter"] = obj
    elif doc.per:
        kw["party"] = obj
    values = catalog.apply_aliases(doc, context.build(case, **kw))
    data, missing = engine.fill(doc.template_bytes(), values, thai_digits=doc.digits == "thai")
    label = item_label(obj) if obj is not None else ""
    with transaction.atomic():
        filt = {"case": case, "key": doc.key, "party": kw.get("party"), "letter": kw.get("letter")}
        version = (IssuedDocument.objects.select_for_update().filter(**filt).aggregate(m=Max("version"))["m"] or 0) + 1
        issued = IssuedDocument(
            case=case, job=job, key=doc.key, title=doc.title, party=kw.get("party"), letter=kw.get("letter"), item_label=label,
            version=version, template_version=doc.template_version(), missing_fields=missing, created_by=job.created_by,
        )  # fmt: skip
        issued.docx.save(_filename(doc, label, version, "docx"), ContentFile(data), save=False)
        if job.formats in ("pdf", "both"):
            try:
                issued.pdf.save(_filename(doc, label, version, "pdf"), ContentFile(docx_to_pdf(data)), save=False)
            except RuntimeError as e:  # no LibreOffice on this machine: keep the Word file
                job.pdf_warning = f"{e} จึงได้เฉพาะไฟล์ Word"
        issued.save()
    return issued


def run_job(job_id: int) -> DocumentJob:
    from deadlines.services import log_action

    job = DocumentJob.objects.select_related("case", "created_by").get(pk=job_id)
    job.status = DocumentJob.Status.RUNNING
    job.save(update_fields=["status"])
    case, made, errors = job.case, [], []
    _, st = states(case)
    by_key = {s.doc.key: s for s in st}
    try:
        for sel in job.request:
            state = by_key.get(sel.get("key"))
            if state is None:
                continue
            if state.missing:
                errors.append(f"{state.doc.title}: ยังไม่พร้อม ({', '.join(m.label for m in state.missing)})")
                continue
            if state.doc.per:
                objs = {o.pk: o for o in candidates(case, state.doc)}
                ready = {i.id for i in state.items if i.ready}
                for item_id in sel.get("items", []):
                    if int(item_id) in ready and int(item_id) in objs:
                        made.append(build_one(job, state.doc, objs[int(item_id)]))
                    else:
                        errors.append(f"{state.doc.title}: ข้ามรายการที่ยังไม่พร้อม")
            else:
                made.append(build_one(job, state.doc))
        want_docx = job.formats in ("docx", "both") or not any(d.pdf for d in made)  # no PDF possible: hand over Word
        files = [(d.docx, d.docx.name) for d in made if want_docx]
        files += [(d.pdf, d.pdf.name) for d in made if d.pdf]
        if len(files) > 1:
            buf = BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f, name in files:
                    with f.open("rb") as fh:
                        z.writestr(name.rsplit("/", 1)[-1], fh.read())
            job.bundle.save(
                f"เอกสาร-คดี{case.pk}-{timezone.localtime():%Y%m%d-%H%M%S}.zip", ContentFile(buf.getvalue()), save=False
            )
        job.status = DocumentJob.Status.DONE if made else DocumentJob.Status.FAILED
        if getattr(job, "pdf_warning", ""):
            errors.append(job.pdf_warning)
        job.error = "\n".join(errors) if errors else ("" if made else "ไม่มีเอกสารที่พร้อมออก")
    except Exception as exc:  # noqa: BLE001 - shown to the officer; details stay in the worker log
        job.status, job.error = DocumentJob.Status.FAILED, f"สร้างเอกสารไม่สำเร็จ: {exc}"
    job.finished_at = timezone.now()
    job.save()
    for d in made:
        who = f" ({d.item_label})" if d.item_label else ""
        log(job.created_by, "export", "document", d.pk, f"ออกเอกสาร {d.title}{who} ฉบับที่ {d.version} คดี {case}")
        log_action(case, job.created_by, f"ออกเอกสาร: {d.title}{who} ฉบับที่ {d.version}", reason="ออกจากระบบ CCID4")
    return job


def bundle_files(job: DocumentJob) -> list:
    """The single file to download when the job made only one."""
    docs = list(job.documents.all())
    want_docx = job.formats in ("docx", "both") or not any(d.pdf for d in docs)
    files = [d.docx for d in docs if want_docx] + [d.pdf for d in docs if d.pdf]
    return files
