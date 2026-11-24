import json
import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audit.services import log
from cases.views import can_edit as case_editable
from cases.views import get_case
from parties.models import CaseParty

from . import documents, live, services
from .models import InterviewSession, InterviewSettings, Marker, Participant, QAPair, Segment

FIXTURE = settings.BASE_DIR / "interviews" / "fixtures" / "mule_account_suspect.json"


def session_for(request, pk, edit=False) -> InterviewSession:
    s = get_object_or_404(InterviewSession.objects.select_related("case", "party__person", "created_by"), pk=pk)
    services.require(services.can_view(request.user, s))
    if edit:
        services.require(services.can_edit(request.user, s))
    return s


# ---------------------------------------------------------------- start
@login_required
def start(request, case_pk, party_pk):
    case = get_case(request.user, case_pk)
    services.require(case_editable(request.user, case))
    party = get_object_or_404(CaseParty.objects.select_related("person"), pk=party_pk, case=case)
    roles = [(k, v) for k, v in Participant.Role.choices if k not in ("investigator", "subject")]
    if request.method == "POST":
        people = list(
            zip(request.POST.getlist("role"), request.POST.getlist("name"), request.POST.getlist("language"), strict=False)
        )
        try:
            s = services.start(request.user, party, request.POST.get("place", "").strip() or "ห้องสอบสวน", people)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(f"{case.get_absolute_url()}#suspects")
        return redirect("interviews:record", pk=s.pk)
    existing = party.interviews.all()
    return render(
        request,
        "interviews/start.html",
        {"case": case, "party": party, "roles": roles, "existing": existing, "active": "interviews"},
    )


@login_required
def index(request):
    from cases.models import Case

    visible = Case.objects.visible_to(request.user)
    qs = InterviewSession.objects.filter(case__in=visible).select_related("case", "party__person", "created_by")
    if request.user.role not in ("supervisor", "commander", "admin"):
        qs = qs.filter(created_by=request.user) | qs.filter(case__owner=request.user)
    kinds = [("online", "คดีออนไลน์ (หลอกลวง/ฉ้อโกงทางออนไลน์)"), ("criminal", "คดีอาญาทั่วไป")]
    return render(request, "interviews/index.html", {"sessions": qs.distinct()[:100], "kinds": kinds, "active": "interviews",
                                                     "can_quick": request.user.role in ("investigator", "supervisor", "admin"),
                                                     "import_max_mb": settings.INTERVIEW_IMPORT_MAX_MB})  # fmt: skip


@login_required
@require_POST
def quick_start(request):
    """อัดเสียงผู้แจ้งความรายใหม่: สร้างคดีรับแจ้ง + ผู้กล่าวหา + ห้องสอบ แล้วไปหน้าบันทึกเสียงทันที."""
    if request.user.role not in ("investigator", "supervisor", "admin"):
        raise PermissionDenied
    name = request.POST.get("name", "").strip()
    title = request.POST.get("title", "").strip()
    if len(name) < 2 or len(title) < 3:
        messages.error(request, "กรอกชื่อผู้แจ้งและเรื่องที่แจ้งโดยย่อก่อน")
        return redirect("interviews:index")
    upload = request.FILES.get("audio") if request.POST.get("action") == "import" else None
    if request.POST.get("action") == "import" and not upload:
        messages.error(request, 'เลือกไฟล์เสียงก่อนกด "สร้างและนำเข้าไฟล์เสียง"')
        return redirect("interviews:index")
    s = services.quick_start(request.user, name, request.POST.get("phone", "").strip(), request.POST.get("kind", "online"), title,
                             request.POST.get("place", "").strip() or "ห้องสอบสวน")  # fmt: skip
    if upload:  # a recording made earlier (phone, recorder): lock it and transcribe it straight away
        try:
            services.import_audio(s, request.user, upload)
        except ValidationError as e:
            messages.error(
                request, f"สร้างคดีและห้องสอบปากคำแล้ว แต่นำเข้าไฟล์ไม่ได้: {' '.join(e.messages)} · เลือกไฟล์ใหม่ได้ในหน้านี้"
            )
            return redirect("interviews:record", pk=s.pk)
        messages.success(request, f"สร้างคดีรับแจ้งของ {s.party.person.full_name} และนำเข้าไฟล์เสียงแล้ว · กำลังถอดเป็นคำพูด")
        return redirect("interviews:process", pk=s.pk)
    messages.success(request, f"สร้างคดีรับแจ้งและห้องสอบปากคำของ {s.party.person.full_name} แล้ว เริ่มบันทึกเสียงได้เลย")
    return redirect("interviews:record", pk=s.pk)


# ---------------------------------------------------------------- recording
@login_required
def record(request, pk):
    s = session_for(request, pk)
    if hasattr(s, "recording"):
        return redirect("interviews:review" if s.status in ("review", "confirmed") else "interviews:process", pk=s.pk)
    ctx = {"s": s, "status": services.uploaded(s), "script": InterviewSettings.get().rights_script, "can_edit": services.can_edit(request.user, s),
           "markers": s.markers.all(), "participants": s.participants.all(), "active": "interviews",
           "import_max_mb": settings.INTERVIEW_IMPORT_MAX_MB}  # fmt: skip
    return render(request, "interviews/record.html", ctx)


@login_required
@require_POST
def chunk(request, pk):
    s = session_for(request, pk, edit=True)
    try:
        take, seq = int(request.POST["take"]), int(request.POST["seq"])
        seconds = float(request.POST.get("seconds") or 5)
        data = request.FILES["blob"].read()
        return JsonResponse(services.add_chunk(s, request.user, take, seq, data, seconds))
    except (KeyError, ValueError):
        return JsonResponse({"error": "ข้อมูลท่อนเสียงไม่ครบ"}, status=400)
    except ValidationError as e:
        return JsonResponse({"error": " ".join(e.messages)}, status=409)


@login_required
def status(request, pk):
    s = session_for(request, pk)
    return JsonResponse(services.uploaded(s) | {"closed": hasattr(s, "recording"), "state": s.status})


@login_required
def live_text(request, pk):
    """Text transcribed while recording. GET ?after=<id>: new lines · POST: the officer paused/stopped, do the tail."""
    s = session_for(request, pk, edit=request.method == "POST")
    if request.method == "POST":
        live.queue(s, flush=True)
        return JsonResponse({"queued": live.enabled()})
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    return JsonResponse(live.payload(s, after))


@login_required
@require_POST
def marker(request, pk):
    s = session_for(request, pk, edit=True)
    try:
        at = float(request.POST.get("at", 0))
    except ValueError:
        at = 0
    m = Marker.objects.create(session=s, at=max(0, at), note=request.POST.get("note", "")[:200], created_by=request.user)
    return JsonResponse({"id": m.pk, "at": m.at, "note": m.note})


@login_required
@require_POST
def import_audio(request, pk):
    s = session_for(request, pk, edit=True)
    upload = request.FILES.get("audio")
    if not upload:
        messages.error(request, "เลือกไฟล์เสียงก่อน")
        return redirect("interviews:record", pk=s.pk)
    try:
        services.import_audio(s, request.user, upload)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("interviews:record", pk=s.pk)
    messages.success(request, "นำเข้าไฟล์เสียงแล้ว ไฟล์ต้นฉบับถูกล็อกพร้อม SHA-256 · กำลังถอดเสียง")
    return redirect("interviews:process", pk=s.pk)


@login_required
@require_POST
def close(request, pk):
    s = session_for(request, pk, edit=True)
    try:
        services.close_recording(s, request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("interviews:record", pk=s.pk)
    return redirect("interviews:process", pk=s.pk)


# ---------------------------------------------------------------- processing
@login_required
def process_page(request, pk):
    s = session_for(request, pk)
    if s.status in ("review", "confirmed"):
        if request.headers.get("HX-Request"):
            return HttpResponse(status=204, headers={"HX-Redirect": f"/interviews/{s.pk}/review/"})
        return redirect("interviews:review", pk=s.pk)
    idle = services.minutes_without_progress(s)
    ctx = {"s": s, "rec": getattr(s, "recording", None), "demo": settings.DEMO_MODE, "active": "interviews",
           "idle": idle, "stalled": idle is not None and idle >= services.STALL_MINUTES,
           "can_edit": services.can_edit(request.user, s), "can_restart": services.can_restart(s)}  # fmt: skip
    tpl = "interviews/_progress.html" if request.headers.get("HX-Request") else "interviews/process.html"
    return render(request, tpl, ctx)


@login_required
@require_POST
def reprocess(request, pk):
    s = session_for(request, pk, edit=True)
    try:
        services.restart_processing(s, request.user)
        messages.success(request, "เริ่มถอดเสียงใหม่แล้ว")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("interviews:process", pk=s.pk)


@login_required
@require_POST
def import_transcript(request, pk):
    s = session_for(request, pk, edit=True)
    if not hasattr(s, "recording"):
        raise Http404
    text = request.POST.get("text", "")
    if request.FILES.get("file"):
        text = request.FILES["file"].read().decode("utf-8", errors="replace")
    if request.POST.get("demo") and settings.DEMO_MODE:
        text = FIXTURE.read_text(encoding="utf-8")
    try:
        services.import_transcript(s, request.user, text)
    except (ValidationError, services.llm.AIError) as e:
        messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
        return redirect("interviews:process", pk=s.pk)
    return redirect("interviews:review", pk=s.pk)


# ---------------------------------------------------------------- audio (permission-checked, seekable, audited)
RANGE = re.compile(r"bytes=(\d+)-(\d*)")


@login_required
def audio(request, pk):
    s = session_for(request, pk)
    rec = getattr(s, "recording", None)
    if rec is None:
        raise Http404
    path, content_type = services.playable(rec)
    size = os.path.getsize(path)
    m = RANGE.match(request.headers.get("Range", ""))
    if not m or m.group(1) == "0":
        log(request.user, "listen", "interview", s.pk, f"ฟังเสียงสอบปากคำ {s}")
    if not m:
        resp = FileResponse(open(path, "rb"), content_type=content_type)  # noqa: SIM115 - closed by Django
        resp["Accept-Ranges"] = "bytes"
        return resp
    start, end = int(m.group(1)), int(m.group(2) or size - 1)
    end = min(end, size - 1)
    if start > end:
        return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})

    def stream():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                block = f.read(min(1 << 16, left))
                if not block:
                    break
                left -= len(block)
                yield block

    resp = StreamingHttpResponse(stream(), status=206, content_type=content_type)
    resp["Content-Range"], resp["Content-Length"], resp["Accept-Ranges"] = (
        f"bytes {start}-{end}/{size}",
        str(end - start + 1),
        "bytes",
    )
    return resp


# ---------------------------------------------------------------- review
def review_ctx(request, s):
    draft = services.current_draft(s)
    roles = dict(s.speaker_maps.values_list("label", "role"))
    role_names = dict(Participant.Role.choices)
    segments = list(s.segments.prefetch_related("edits"))
    for seg in segments:
        seg.role = roles.get(seg.speaker, "")
        seg.role_name = role_names.get(seg.role, seg.speaker)
        seg.word_list = services.display_words(seg.text, seg.words if seg.text == seg.text_ai else None)
    pairs = list(draft.pairs.all()) if draft else []
    rights_ok = s.rights.exists() and not s.rights.filter(found_at__isnull=True).exists()
    return {
        "s": s, "rec": s.recording, "segments": segments, "draft": draft, "pairs": pairs, "maps": s.speaker_maps.all(),
        "role_choices": Participant.Role.choices, "rights": s.rights.all(), "rights_ok": rights_ok,
        "uncovered": set(draft.uncovered) if draft else set(), "blockers": services.blockers(s), "can_edit": services.can_edit(request.user, s),
        "asr_profile": services.asr.model_label(), "turns": services.verbatim_turns(s),
        "reviewed": sum(1 for p in pairs if p.reviewed), "markers": s.markers.all(), "documents": s.documents.all(), "active": "interviews",
        "speaker_options": speaker_options(s),
    }  # fmt: skip


def speaker_options(s) -> list[tuple[str, str]]:
    names = dict(Participant.Role.choices)
    roles = dict(s.speaker_maps.values_list("label", "role"))
    labels = sorted(set(roles) | {"S1", "S2"})
    defaults = {"S1": "investigator", "S2": "subject"}
    return [(lab, f"{lab} · {names.get(roles.get(lab) or defaults.get(lab, ''), 'ไม่ระบุ')}") for lab in labels]


@login_required
def review(request, pk):
    s = session_for(request, pk)
    if s.status in ("draft", "recording"):
        return redirect("interviews:record", pk=s.pk)
    if not s.segments.exists():
        return redirect("interviews:process", pk=s.pk)
    log(request.user, "view", "interview", s.pk, f"เปิดหน้าตรวจคำให้การจากเสียง {s}")
    return render(request, "interviews/review.html", review_ctx(request, s))


@login_required
@require_POST
def edit_segment(request, pk, seg_id):
    s = session_for(request, pk, edit=True)
    seg = get_object_or_404(Segment, pk=seg_id, session=s)
    services.edit_segment(seg, request.user, request.POST.get("text", ""))
    return segment_partial(request, s, seg.pk)


@login_required
@require_POST
def segment_speaker(request, pk, seg_id):
    s = session_for(request, pk, edit=True)
    seg = get_object_or_404(Segment, pk=seg_id, session=s)
    services.set_segment_speaker(seg, request.user, request.POST.get("speaker", ""))
    return segment_partial(request, s, seg.pk, note="เปลี่ยนผู้พูดแล้ว — กด “ร่างใหม่” เพื่อปรับคำให้การและสรุป")


def segment_partial(request, s, seg_id, note=""):
    seg = Segment.objects.prefetch_related("edits").get(pk=seg_id)
    roles = dict(s.speaker_maps.values_list("label", "role"))
    seg.role = roles.get(seg.speaker, "")
    seg.role_name = dict(Participant.Role.choices).get(seg.role, seg.speaker)
    seg.word_list = services.display_words(seg.text, seg.words if seg.text == seg.text_ai else None)
    ctx = {"seg": seg, "s": s, "can_edit": True, "speaker_options": speaker_options(s), "note": note}
    return render(request, "interviews/_segment.html", ctx)


@login_required
@require_POST
def narrative(request, pk):
    s = session_for(request, pk, edit=True)
    draft = services.current_draft(s)
    if not draft or draft.locked:
        raise PermissionDenied
    services.update_narrative(
        draft, request.user, request.POST.get("narrative", draft.narrative), request.POST.get("reviewed") == "on"
    )
    from django.template.loader import render_to_string

    html = render_to_string("interviews/_narrative.html", {"s": s, "draft": draft, "can_edit": True, "saved": True}, request)
    html += render_to_string("interviews/_blockers.html", {"blockers": services.blockers(s), "oob": True}, request)
    return HttpResponse(html)


@login_required
@require_POST
def statement_format(request, pk):
    s = session_for(request, pk, edit=True)
    if request.POST.get("format") in ("narrative", "qa") and s.status != InterviewSession.Status.CONFIRMED:
        s.statement_format = request.POST["format"]
        s.save(update_fields=["statement_format"])
        log(request.user, "edit", "interview", s.pk, f"เลือกรูปแบบคำให้การ: {s.get_statement_format_display()}")
        messages.success(request, f"ใช้คำให้การรูปแบบ{s.get_statement_format_display()}")
    return redirect("interviews:review", pk=s.pk)


@login_required
def transcript_download(request, pk):
    """คำพูดคำต่อคำ (Word): available as soon as the audio is transcribed, before the statement is confirmed."""
    s = session_for(request, pk)
    if not hasattr(s, "recording") or not s.segments.exists():
        raise Http404
    data = documents.transcript_docx(s)
    log(request.user, "export", "interview", s.pk, "ดาวน์โหลดข้อความถอดเสียงคำต่อคำ")
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    from urllib.parse import quote

    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'ข้อความถอดเสียง-{s.pk}.docx')}"
    return resp


@login_required
def summary_download(request, pk):
    s = session_for(request, pk)
    draft = s.drafts.filter(locked=True).first() or services.current_draft(s)
    if not draft:
        raise Http404
    data = documents.summary_docx(s, draft)
    log(request.user, "export", "interview", s.pk, f"ดาวน์โหลดสรุปการสอบปากคำ (ร่างฉบับที่ {draft.version})")
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    from urllib.parse import quote

    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'สรุปการสอบปากคำ-{s.pk}-ร่าง{draft.version}.docx')}"
    return resp


@login_required
@require_POST
def speakers(request, pk):
    s = session_for(request, pk, edit=True)
    services.set_speakers(s, request.user, {m.label: request.POST.get(f"role-{m.label}", m.role) for m in s.speaker_maps.all()})
    services.analyse(s, request.user)
    messages.success(request, "กำหนดผู้พูดแล้ว ระบบตรวจสิทธิและร่างถาม–ตอบใหม่")
    return redirect("interviews:review", pk=s.pk)


@login_required
@require_POST
def redraft(request, pk):
    s = session_for(request, pk, edit=True)
    if request.POST.get("mode") in InterviewSession.Mode.values:
        s.mode = request.POST["mode"]
        s.save(update_fields=["mode"])
    services.build_draft(s, request.user)
    messages.success(request, f"ร่างใหม่แบบ{s.get_mode_display()}แล้ว (ฉบับก่อนหน้ายังเก็บไว้)")
    return redirect("interviews:review", pk=s.pk)


@login_required
@require_POST
def save_pair(request, pk, pair_id):
    s = session_for(request, pk, edit=True)
    pair = get_object_or_404(QAPair, pk=pair_id, draft__session=s, draft__locked=False)
    services.update_pair(pair, request.user, request.POST.get("question", pair.question), request.POST.get("answer", pair.answer),
                         request.POST.get("reviewed") == "on")  # fmt: skip
    return render(request, "interviews/_pair.html", {"p": pair, "s": s, "can_edit": True})


@login_required
@require_POST
def confirm(request, pk):
    s = session_for(request, pk, edit=True)
    try:
        services.confirm(s, request.user, request.POST.get("override", "").strip())
    except ValidationError as e:
        for m in e.messages:
            messages.error(request, m)
        return redirect("interviews:review", pk=s.pk)
    messages.success(request, "ยืนยันแล้ว คำให้การของบุคคลนี้ในคดีถูกแทนที่ด้วยฉบับจากเสียง และกำลังสร้างเอกสาร")
    return redirect("interviews:review", pk=s.pk)


@login_required
def download(request, pk, doc_id, kind):
    s = session_for(request, pk)
    doc = get_object_or_404(s.documents, pk=doc_id)
    f = {"docx": doc.docx, "pdf": doc.pdf, "zip": doc.evidence_zip}.get(kind)
    if not f:
        raise Http404
    log(
        request.user,
        "download",
        "interview",
        s.pk,
        f"ดาวน์โหลด{({'docx': 'คำให้การ Word', 'pdf': 'คำให้การ PDF', 'zip': 'ชุดหลักฐาน'})[kind]} {doc.code}",
    )
    return FileResponse(f.open("rb"), as_attachment=True, filename=f.name.rsplit("/", 1)[-1])


# ---------------------------------------------------------------- settings
@login_required
def settings_page(request):
    if request.user.role not in ("supervisor", "admin"):
        raise PermissionDenied
    conf = InterviewSettings.get()
    if request.method == "POST":
        if request.FILES.get("template"):
            from django.core.files.base import ContentFile
            from django.core.files.storage import default_storage

            from documents.engine import find_placeholders

            data = request.FILES["template"].read()
            try:
                names = find_placeholders(data)
            except Exception:  # noqa: BLE001
                messages.error(request, "เปิดไฟล์ Word ไม่ได้")
                return redirect("interviews:settings")
            if default_storage.exists(documents.CUSTOM_TEMPLATE):
                default_storage.delete(documents.CUSTOM_TEMPLATE)
            default_storage.save(documents.CUSTOM_TEMPLATE, ContentFile(data))
            unknown = [n for n in names if n not in documents.PLACEHOLDERS and not n.startswith("qa.")]
            log(request.user, "edit", "interview_settings", "", "อัปโหลดแบบฟอร์มคำให้การจากเสียง")
            messages.success(
                request, "ใช้แบบฟอร์มใหม่แล้ว" + (f" · ตัวแทนที่ระบบไม่รู้จัก: {', '.join(unknown)}" if unknown else "")
            )
        else:
            conf.rights_script = request.POST.get("rights_script", conf.rights_script)
            conf.retention_days = int(request.POST.get("retention_days") or conf.retention_days)
            conf.save()
            log(request.user, "edit", "interview_settings", "", "แก้สคริปต์แจ้งสิทธิ/ระยะเก็บไฟล์เสียง")
            messages.success(request, "บันทึกแล้ว")
        return redirect("interviews:settings")
    return render(
        request, "interviews/settings.html", {"conf": conf, "placeholders": documents.PLACEHOLDERS, "active": "interviews"}
    )


def as_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
