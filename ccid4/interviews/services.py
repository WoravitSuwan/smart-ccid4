"""ห้องสอบปากคำด้วยเสียง: บันทึก → ปิด (SHA-256) → ถอดเสียง → แยกผู้พูด → ตรวจสิทธิ → ร่างถาม–ตอบ → ตรวจต้นทาง → ยืนยัน."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile, File
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from ai import asr, llm
from audit.services import log

from . import qa
from .models import (
    InterviewSession,
    Participant,
    QAPair,
    Recording,
    RecordingChunk,
    RightsCheck,
    Segment,
    SegmentEdit,
    SpeakerMap,
    StatementDraft,
)

STEPS = [
    ("merge", "รวมท่อนเสียงและคำนวณ SHA-256"),
    ("asr", "ถอดเสียง (ตัดช่วงเงียบด้วย VAD)"),
    ("diarize", "แยกผู้พูด"),
    ("align", "จับข้อความกับผู้พูด"),
    ("rights", "ตรวจการแจ้งสิทธิ"),
    ("draft", "ร่างถาม–ตอบ"),
    ("verify", "ตรวจความตรงกับต้นทาง"),
]
MAX_CHUNK = 5 * 1024 * 1024


# ---------------------------------------------------------------- access
def can_view(user, session: InterviewSession) -> bool:
    from cases.models import Case

    if not Case.objects.visible_to(user).filter(pk=session.case_id).exists():
        return False
    return (
        session.created_by_id == user.pk or session.case.owner_id == user.pk or user.role in ("supervisor", "commander", "admin")
    )


def can_edit(user, session: InterviewSession) -> bool:
    from cases.views import can_edit as case_editable

    return case_editable(user, session.case) and not session.locked


def require(ok: bool):
    if not ok:
        raise PermissionDenied


# ---------------------------------------------------------------- session
@transaction.atomic
def start(user, party, place: str, people: list[tuple[str, str, str]]) -> InterviewSession:
    if party.role == "suspect" and not party.rights_informed_at:
        raise ValidationError("ผู้ต้องหา: ต้องแจ้งข้อกล่าวหาและสิทธิ (A4.4) ก่อนเริ่มบันทึก")
    s = InterviewSession.objects.create(case=party.case, party=party, place=place, created_by=user)
    Participant.objects.create(session=s, role=Participant.Role.INVESTIGATOR, name=user.display_name)
    Participant.objects.create(session=s, role=Participant.Role.SUBJECT, name=party.person.full_name)
    for role, name, language in people:
        if name.strip() and role in Participant.Role.values:
            Participant.objects.create(session=s, role=role, name=name.strip()[:200], language=language.strip()[:60])
    log(user, "create", "interview", s.pk, f"สร้างการสอบปากคำด้วยเสียง {party.get_role_display()} {party.person} คดี {party.case}")
    return s


def uploaded(session) -> dict:
    agg = session.chunks.aggregate(seconds=Sum("seconds"), take=Max("take"), n=Sum("size"))
    last_seq = session.chunks.filter(take=agg["take"] or 0).aggregate(m=Max("seq"))["m"] if agg["take"] else None
    return {"seconds": round(agg["seconds"] or 0, 1), "take": agg["take"] or 0, "last_seq": last_seq, "bytes": agg["n"] or 0}


def add_chunk(session, user, take: int, seq: int, data: bytes, seconds: float) -> dict:
    """Idempotent: the browser may resend a chunk after a network hiccup."""
    if hasattr(session, "recording"):
        raise ValidationError("ปิดการบันทึกแล้ว")
    if not data or len(data) > MAX_CHUNK:
        raise ValidationError("ท่อนเสียงว่างหรือใหญ่เกินไป")
    if not RecordingChunk.objects.filter(session=session, take=take, seq=seq).exists():
        c = RecordingChunk(session=session, take=take, seq=seq, size=len(data), seconds=max(0.0, min(seconds, 60.0)))
        c.file.save(f"t{take:03d}-{seq:06d}.webm", ContentFile(data), save=False)
        c.save()
    if session.status == InterviewSession.Status.DRAFT:
        session.status = InterviewSession.Status.RECORDING
        session.save(update_fields=["status"])
        log(user, "edit", "interview", session.pk, "เริ่มบันทึกเสียงสอบปากคำ")
    return uploaded(session)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def merge_takes(session, workdir: Path) -> Path:
    """Each take is a complete WebM (its first chunk has the header); takes are joined without re-encoding."""
    takes = sorted(set(session.chunks.values_list("take", flat=True)))
    files = []
    for take in takes:
        p = workdir / f"take{take}.webm"
        with open(p, "wb") as out:
            for c in session.chunks.filter(take=take).order_by("seq"):
                with c.file.open("rb") as f:
                    shutil.copyfileobj(f, out)
        files.append(p)
    if len(files) == 1:
        return files[0]
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in files))
    merged = workdir / "merged.webm"
    proc = subprocess.run([settings.FFMPEG_BIN, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(merged)],
                          cwd=workdir, capture_output=True, timeout=3600, check=False)  # fmt: skip
    if proc.returncode != 0:  # mismatched takes: re-encode once to a single Opus stream
        subprocess.run([settings.FFMPEG_BIN, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libopus", str(merged)],
                       cwd=workdir, check=True, timeout=3600)  # fmt: skip
    return merged


def close_recording(session, user) -> Recording:
    """รวมท่อน → ไฟล์ต้นฉบับ → SHA-256 → ล็อก (อ่านอย่างเดียว) → เข้าคิวประมวลผล."""
    if hasattr(session, "recording"):
        raise ValidationError("ปิดการบันทึกไปแล้ว")
    if not session.chunks.exists():
        raise ValidationError("ยังไม่มีเสียงที่บันทึก")
    with tempfile.TemporaryDirectory(prefix="ccid4-rec-") as tmp:
        merged = merge_takes(session, Path(tmp))
        digest = sha256_file(merged)
        rec = Recording(session=session, sha256=digest, size=merged.stat().st_size, duration=asr.duration(merged), closed_by=user)
        with open(merged, "rb") as f:
            rec.original.save(f"original-{digest[:12]}.webm", File(f), save=False)
        try:
            flac = asr.to_flac(merged, Path(tmp) / "asr.flac")
            with open(flac, "rb") as f:
                rec.for_asr.save("asr-16k.flac", File(f), save=False)
        except (subprocess.SubprocessError, OSError):
            pass  # transcription can still use the original
        rec.save()
    os.chmod(rec.original.path, 0o444)  # read-only on disk; the app never writes it again
    for c in session.chunks.all():
        c.file.delete(save=False)
    session.chunks.all().delete()
    session.status = InterviewSession.Status.PROCESSING
    session.progress = [
        {"key": k, "label": label, "state": "done" if k == "merge" else "waiting", "note": ""} for k, label in STEPS
    ]
    session.save(update_fields=["status", "progress"])
    log(user, "edit", "interview", session.pk, f"ปิดการบันทึกเสียง ยาว {rec.duration:.0f} วินาที SHA-256 {digest}")
    from .tasks import process_task

    transaction.on_commit(lambda: process_task.delay(session.pk))
    return rec


def integrity_ok(rec: Recording) -> bool:
    return sha256_file(rec.original.path) == rec.sha256


# ---------------------------------------------------------------- pipeline
def set_step(session, key, state, note=""):
    for step in session.progress:
        if step["key"] == key:
            step["state"], step["note"] = state, note
    session.save(update_fields=["progress"])


def process(session_id: int) -> InterviewSession:
    session = InterviewSession.objects.select_related("party", "recording").get(pk=session_id)
    rec = session.recording
    audio = Path(rec.for_asr.path if rec.for_asr else rec.original.path)
    if not session.progress:
        session.progress = [{"key": k, "label": label, "state": "waiting", "note": ""} for k, label in STEPS]
    if not asr.available():
        set_step(session, "asr", "skipped", "ยังไม่เปิดใช้โมเดลถอดเสียงในเครื่อง · นำเข้าข้อความถอดเสียงแทน")
        session.status = InterviewSession.Status.TRANSCRIPT
        session.save(update_fields=["status"])
        return session
    try:
        set_step(session, "asr", "running")
        segs = asr.transcribe(audio)
        set_step(session, "asr", "done", f"{len(segs)} ช่วงพูด")
        set_step(session, "diarize", "running")
        turns = asr.diarize(audio)
        set_step(session, "diarize", "done" if turns else "skipped", "" if turns else "ไม่มีโมเดลแยกผู้พูด · กำหนดผู้พูดเองในหน้าตรวจ")
        segs = asr.assign_speakers(segs, turns)
        set_step(session, "align", "done")
        save_segments(session, segs)
        analyse(session)
    except llm.AIError as e:
        session.error = str(e)[:400]
        session.status = InterviewSession.Status.TRANSCRIPT
        session.save(update_fields=["error", "status"])
    return session


def save_segments(session, items: list[dict]) -> None:
    with transaction.atomic():
        session.segments.all().delete()
        for i, d in enumerate(sorted(items, key=lambda x: x["start"]), start=1):
            words = d.get("words") or []
            conf = round(sum(w.get("p", 1) for w in words) / len(words), 3) if words else None
            Segment.objects.create(session=session, idx=i, start=d["start"], end=d["end"], speaker=d.get("speaker") or "S1",
                                   text_ai=d["text"], text=d["text"], confidence=conf, words=words)  # fmt: skip
        labels = sorted(set(session.segments.values_list("speaker", flat=True)))
        defaults = {"S1": Participant.Role.INVESTIGATOR, "S2": Participant.Role.SUBJECT}
        for lab in labels:
            SpeakerMap.objects.get_or_create(session=session, label=lab, defaults={"role": defaults.get(lab, "")})


def import_transcript(session, user, text: str) -> None:
    items = asr.parse_transcript(text)
    if not items:
        raise ValidationError("ไม่พบข้อความในไฟล์")
    save_segments(session, items)
    log(user, "import", "interview", session.pk, f"นำเข้าข้อความถอดเสียง {len(items)} ช่วงพูด")
    for key, note in (("asr", "นำเข้าจากไฟล์ถอดเสียง"), ("diarize", "ตามป้ายผู้พูดในไฟล์"), ("align", "")):
        set_step(session, key, "done", note)
    analyse(session, user)


def segs_of(session) -> list[qa.Seg]:
    roles = dict(session.speaker_maps.values_list("label", "role"))
    return [qa.Seg(s.pk, s.start, s.end, roles.get(s.speaker, ""), s.text) for s in session.segments.all()]


def analyse(session, user=None) -> StatementDraft:
    set_step(session, "rights", "running")
    segs = segs_of(session)
    with transaction.atomic():
        session.rights.all().delete()
        for r in qa.check_rights(segs):
            RightsCheck.objects.create(session=session, key=r.key, label=r.label, found_at=r.found_at)
    found = session.rights.filter(found_at__isnull=False).count()
    set_step(session, "rights", "done" if found == len(qa.RIGHTS) else "warning", f"พบ {found}/{len(qa.RIGHTS)} ข้อ")
    draft = build_draft(session, user)
    session.status = InterviewSession.Status.REVIEW
    session.error = ""
    session.save(update_fields=["status", "error"])
    return draft


def build_draft(session, user=None) -> StatementDraft:
    set_step(session, "draft", "running")
    segs = segs_of(session)
    by_id = {s.id: s for s in segs}
    source = "code"
    pairs = qa.build_pairs(segs)
    if session.mode == InterviewSession.Mode.WRITTEN or settings.AI_BACKEND == "ollama":
        ctx = [{"id": s.id, "role": s.role or "other", "start": s.start, "end": s.end, "text": s.text} for s in segs]
        extra = {"role_label": session.party.get_role_display(), "mode_label": session.get_mode_display(),
                 "mode_rule": "ปรับไวยากรณ์ให้เป็นภาษาเขียนเล็กน้อย" if session.mode == "written" else "คงคำพูดเดิมให้มากที่สุด"}  # fmt: skip
        try:
            out = llm.complete_json("arrange_qa", ctx, user=user, case=session.case, extra=extra)
            pairs = []
            for p in out["pairs"]:
                ids = [i for i in p["segment_ids"] if i in by_id]
                times = [by_id[i] for i in ids]
                pairs.append(
                    qa.Pair(
                        p["question"],
                        p["answer"],
                        min((t.start for t in times), default=0),
                        max((t.end for t in times), default=0),
                        ids,
                    )
                )
            source = "ai"
        except llm.AIError as e:
            set_step(session, "draft", "warning", f"AI เรียบเรียงไม่ได้ ใช้การจับคู่ตามคำพูด: {e}")
    set_step(session, "verify", "running")
    for p in pairs:
        p.flags = qa.verify(p, by_id, all_segments=segs)
    with transaction.atomic():
        version = (session.drafts.aggregate(m=Max("version"))["m"] or 0) + 1
        draft = StatementDraft.objects.create(session=session, version=version, mode=session.mode, source=source, created_by=user,
                                              uncovered=qa.uncovered(segs, pairs))  # fmt: skip
        QAPair.objects.bulk_create([QAPair(draft=draft, order=i, question=p.question, answer=p.answer, start=p.start, end=p.end,
                                           segment_ids=p.segment_ids, flags=p.flags) for i, p in enumerate(pairs, start=1)])  # fmt: skip
    flagged = sum(1 for p in pairs if p.flags)
    set_step(session, "draft", "done", f"{len(pairs)} คู่ ({'AI' if source == 'ai' else 'จับคู่ตามคำพูด'})")
    set_step(
        session, "verify", "done" if not flagged else "warning", f"ติดธง {flagged} คู่ · ช่วงที่ไม่อยู่ในคำตอบ {len(draft.uncovered)}"
    )
    return draft


def current_draft(session) -> StatementDraft | None:
    return session.drafts.first()


# ---------------------------------------------------------------- review actions
def edit_segment(seg: Segment, user, text: str) -> None:
    text = (text or "").strip()
    if not text or text == seg.text:
        return
    SegmentEdit.objects.create(segment=seg, old=seg.text, new=text, by=user)
    seg.text = text
    seg.save(update_fields=["text"])
    log(user, "edit", "interview_segment", seg.pk, f"แก้ข้อความถอดเสียงช่วง {seg.idx} (เก็บต้นฉบับ AI ไว้)")


def set_speakers(session, user, mapping: dict[str, str]) -> None:
    for label, role in mapping.items():
        if role in Participant.Role.values or role == "":
            SpeakerMap.objects.filter(session=session, label=label).update(role=role)
    log(user, "edit", "interview", session.pk, "กำหนดผู้พูด")


def update_pair(pair: QAPair, user, question: str, answer: str, reviewed: bool) -> None:
    session = pair.draft.session
    pair.question, pair.answer, pair.reviewed = question.strip(), answer.strip(), reviewed
    segs = segs_of(session)
    p = qa.Pair(pair.question, pair.answer, pair.start, pair.end, pair.segment_ids)
    pair.flags = qa.verify(p, {s.id: s for s in segs}, all_segments=segs)
    pair.save()
    log(user, "edit", "interview_pair", pair.pk, f"ตรวจ/แก้คู่ถาม–ตอบที่ {pair.order}")


def blockers(session, override: str = "") -> list[str]:
    draft = current_draft(session)
    reviewed = list(draft.pairs.values_list("reviewed", flat=True)) if draft else []
    rights_ok = session.rights.exists() and not session.rights.filter(found_at__isnull=True).exists()
    return qa.confirm_blockers(reviewed, rights_ok, override or session.rights_override, session.is_suspect)


def statement_text(draft: StatementDraft) -> str:
    return "\n".join(f"ถาม {p.question}\nตอบ {p.answer}" for p in draft.pairs.all())


@transaction.atomic
def confirm(session, user, override: str = ""):
    from deadlines.services import log_action, sync_rule_tasks
    from parties.models import Statement

    problems = blockers(session, override)
    if problems:
        raise ValidationError(problems)
    draft = current_draft(session)
    draft.locked = True
    draft.save(update_fields=["locked"])
    cp = session.party
    version = (cp.statements.aggregate(m=Max("version"))["m"] or 0) + 1
    st = Statement.objects.create(party=cp, version=version, text=statement_text(draft), source=Statement.Source.VOICE,
                                  created_by=user, confirmed_by=user, confirmed_at=timezone.now())  # fmt: skip
    session.statement, session.status = st, InterviewSession.Status.CONFIRMED
    session.confirmed_by, session.confirmed_at = user, timezone.now()
    if override:
        session.rights_override = override
    session.save()
    log(user, "confirm", "interview", session.pk, f"ยืนยันคำให้การจากเสียง {cp.person} ฉบับร่าง {draft.version}"
        + (f" (ยืนยันแม้ไม่พบการแจ้งสิทธิครบ: {override})" if override else ""))  # fmt: skip
    log_action(session.case, user, f"สอบปากคำ{cp.get_role_display()} {cp.person.full_name} ด้วยเสียง ยืนยันคำให้การฉบับที่ {version}",
               reason=f"ไฟล์เสียง SHA-256 {session.recording.sha256}")  # fmt: skip
    sync_rule_tasks(session.case)
    from .documents import generate

    transaction.on_commit(lambda: generate(session.pk, user.pk))
    return st


def transcript_json(session) -> dict:
    roles = dict(session.speaker_maps.values_list("label", "role"))
    return {
        "session": session.pk, "case": str(session.case), "subject": session.party.person.full_name,
        "audio_sha256": session.recording.sha256, "duration": session.recording.duration,
        "segments": [{"idx": s.idx, "start": s.start, "end": s.end, "speaker": s.speaker, "role": roles.get(s.speaker, ""),
                      "text_ai": s.text_ai, "text": s.text, "confidence": s.confidence} for s in session.segments.all()],
    }  # fmt: skip


def dumps(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=1).encode()
