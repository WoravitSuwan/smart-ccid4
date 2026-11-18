"""ห้องสอบปากคำด้วยเสียง: บันทึก → ปิด (SHA-256) → ถอดเสียง → แยกผู้พูด → ตรวจสิทธิ → ร่างถาม–ตอบ → ตรวจต้นทาง → ยืนยัน."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile, File
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from ai import asr, llm
from audit.services import log
from core import thai

from . import qa, summary
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
    s = InterviewSession.objects.create(case=party.case, party=party, place=place, created_by=user,
                                        statement_format="qa" if party.role == "suspect" else "narrative")  # fmt: skip
    Participant.objects.create(session=s, role=Participant.Role.INVESTIGATOR, name=user.display_name)
    Participant.objects.create(session=s, role=Participant.Role.SUBJECT, name=party.person.full_name)
    for role, name, language in people:
        if name.strip() and role in Participant.Role.values:
            Participant.objects.create(session=s, role=role, name=name.strip()[:200], language=language.strip()[:60])
    log(
        user, "create", "interview", s.pk, f"สร้างการสอบปากคำด้วยเสียง {party.get_role_display()} {party.person} คดี {party.case}"
    )
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
        # browser WebM often carries no duration header: fall back to the seconds counted while recording
        seconds = asr.duration(merged) or uploaded(session)["seconds"]
        rec = Recording(session=session, sha256=digest, size=merged.stat().st_size, duration=seconds, closed_by=user)
        with open(merged, "rb") as f:
            rec.original.save(f"original-{digest[:12]}.webm", File(f), save=False)
        rec.save()  # the 16 kHz copy for the speech model is made by the worker (long recordings take a while)
    os.chmod(rec.original.path, 0o444)  # read-only on disk; the app never writes it again
    from evidence.services import register_stored

    register_stored(session.case, user, rec.original.name, digest, rec.size,
                    f"เสียงสอบปากคำ{session.party.get_role_display()} {session.party.person.full_name}", "audio/webm",
                    "ห้องสอบปากคำ (ไฟล์เสียงต้นฉบับ)", "interview")  # fmt: skip
    for c in session.chunks.all():
        c.file.delete(save=False)
    session.chunks.all().delete()
    log(user, "edit", "interview", session.pk, f"ปิดการบันทึกเสียง ยาว {rec.duration:.0f} วินาที SHA-256 {digest}")
    start_job(session)
    return rec


def start_job(session) -> str:
    """Queue the speech pipeline. A new job id makes any older run of this session stop at its next step."""
    session.status = InterviewSession.Status.PROCESSING
    session.error = ""
    session.job = uuid.uuid4().hex
    now = timezone.now().isoformat(timespec="seconds")
    session.progress = [
        {"key": k, "label": label, "state": "done" if k == "merge" else "waiting", "note": "", "at": now} for k, label in STEPS
    ]
    session.save(update_fields=["status", "error", "job", "progress"])
    from .tasks import process_task

    job = session.job
    transaction.on_commit(lambda: process_task.delay(session.pk, job))
    return job


STALL_MINUTES = 5  # no progress for this long while "processing": the worker probably stopped


def minutes_without_progress(session) -> int | None:
    """Minutes since the pipeline last reported anything (None when it is not processing)."""
    if session.status != InterviewSession.Status.PROCESSING:
        return None
    stamps = [st.get("at") for st in session.progress or [] if st.get("at")]
    last = max(stamps) if stamps else None
    if last:
        from datetime import datetime

        last_at = datetime.fromisoformat(last)
    else:  # processed by an older version that kept no timestamps
        rec = getattr(session, "recording", None)
        last_at = rec.closed_at if rec else session.created_at
    return max(0, int((timezone.now() - last_at).total_seconds() // 60))


def restart_processing(session, user) -> None:
    if session.status != InterviewSession.Status.PROCESSING or not hasattr(session, "recording"):
        raise ValidationError("เริ่มถอดเสียงใหม่ได้เฉพาะเสียงที่ปิดการบันทึกแล้วและยังประมวลผลไม่เสร็จ")
    minutes = minutes_without_progress(session)
    log(user, "edit", "interview", session.pk, f"เริ่มประมวลผลเสียงใหม่ (ไม่มีความคืบหน้า {minutes} นาที)")
    start_job(session)


def integrity_ok(rec: Recording) -> bool:
    return sha256_file(rec.original.path) == rec.sha256


# ---------------------------------------------------------------- pipeline
class Superseded(Exception):
    """The officer started this session's processing again: this older run stops quietly."""


def set_step(session, key, state, note=""):
    if session.job and not InterviewSession.objects.filter(pk=session.pk, job=session.job).exists():
        raise Superseded
    now = timezone.now().isoformat(timespec="seconds")
    for step in session.progress:
        if step["key"] == key:
            step["state"], step["note"], step["at"] = state, note, now
    session.save(update_fields=["progress"])


def process(session_id: int, job: str | None = None) -> InterviewSession:
    session = InterviewSession.objects.select_related("party", "recording").get(pk=session_id)
    if job and session.job != job:
        return session  # a newer run was started after this one was queued
    try:
        return _process(session)
    except Superseded:
        return session


def _process(session) -> InterviewSession:
    rec = session.recording
    if not rec.for_asr:
        with tempfile.TemporaryDirectory(prefix="ccid4-asr-") as tmp:
            try:
                flac = asr.to_flac(Path(rec.original.path), Path(tmp) / "asr.flac")
                with open(flac, "rb") as f:
                    rec.for_asr.save("asr-16k.flac", File(f), save=True)
            except (subprocess.SubprocessError, OSError):
                pass  # transcription can still use the original
    audio = Path(rec.for_asr.path if rec.for_asr else rec.original.path)
    if not session.progress:
        session.progress = [{"key": k, "label": label, "state": "waiting", "note": ""} for k, label in STEPS]
    if not asr.available():
        set_step(session, "asr", "skipped", "ยังไม่เปิดใช้โมเดลถอดเสียงในเครื่อง · นำเข้าข้อความถอดเสียงแทน")
        session.status = InterviewSession.Status.TRANSCRIPT
        session.save(update_fields=["status"])
        return session
    try:
        set_step(
            session, "asr", "running", f"เสียงยาว {thai.mmss(rec.duration)} · กำลังโหลดโมเดลและตัดช่วงเงียบ ({asr.profile()})"
        )
        segs = asr.transcribe(audio, progress=asr_progress(session, rec.duration))
        set_step(session, "asr", "done", f"{len(segs)} ช่วงพูด")
        set_step(session, "diarize", "running")
        turns = asr.diarize(audio)
        set_step(
            session, "diarize", "done" if turns else "skipped", "" if turns else "ไม่มีโมเดลแยกผู้พูด · กำหนดผู้พูดเองในหน้าตรวจ"
        )
        segs = asr.assign_speakers(segs, turns)
        if not turns:  # no speaker model: questions and procedure → officer, the rest → the person giving the statement
            for seg in segs:
                seg["speaker"] = summary.guess_speaker(seg["text"])
        set_step(session, "align", "done")
        save_segments(session, segs)
        analyse(session)
    except llm.AIError as e:
        session.error = str(e)[:400]
        session.status = InterviewSession.Status.TRANSCRIPT
        session.save(update_fields=["error", "status"])
    return session


def asr_progress(session, duration: float):
    """Report ถอดเสียงแล้ว mm:ss จาก mm:ss (xx%) · เหลือประมาณ N นาที, at most every 10 seconds of wall time."""
    last = [0.0]
    started = [None]  # wall time when decoding began (after the model loaded)
    profile = asr.profile()

    def report(done: float, total: float):
        total = total or duration
        now = time.monotonic()
        if started[0] is None:
            started[0] = now
            set_step(session, "asr", "running",
                     f"เริ่มถอดเสียง {thai.mmss(total)} ({profile}) · ช่วงแรกอาจใช้เวลาสักครู่")  # fmt: skip
            last[0] = now
            return
        if now - last[0] < 10:
            return
        last[0] = now
        note = f"ถอดเสียงแล้ว {thai.mmss(done)} จาก {thai.mmss(total)}"
        if total:
            note += f" ({min(99, int(done / total * 100))}%)"
        spent = now - started[0]
        if done > 0 and total > done and spent > 20:
            left = (total - done) * spent / done
            note += f" · เหลือประมาณ {max(1, round(left / 60))} นาที"
        set_step(session, "asr", "running", f"{note} · {profile}")

    return report


def save_segments(session, items: list[dict]) -> None:
    if len(items) > 3 and len({d.get("speaker") or "S1" for d in items}) == 1:  # transcript without speaker labels
        for d in items:
            d["speaker"] = summary.guess_speaker(d["text"])
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
    build_summary(session, draft, pairs, segs, user)
    flagged = sum(1 for p in pairs if p.flags)
    set_step(session, "draft", "done", f"{len(pairs)} คู่ ({'AI' if source == 'ai' else 'จับคู่ตามคำพูด'})")
    set_step(
        session,
        "verify",
        "done" if not flagged else "warning",
        f"ติดธง {flagged} คู่ · ช่วงที่ไม่อยู่ในคำตอบ {len(draft.uncovered)}",
    )
    return draft


def build_summary(session, draft, pairs, segs, user=None) -> None:
    """สรุปใจความสำคัญ + เล่าเรื่อง + คำที่พบบ่อย (all from the recording; checked by code)."""
    points, narrative, source = summary.code_points(pairs, segs), summary.code_narrative(pairs, segs), "code"
    if settings.AI_BACKEND == "ollama":
        ctx = [{"id": s.id, "role": s.role or "other", "start": s.start, "text": s.text} for s in segs]
        extra = {"role_label": session.party.get_role_display(), "mode_label": session.get_mode_display()}
        try:
            out = llm.complete_json("summarize_interview", ctx, user=user, case=session.case, extra=extra)
            points, narrative, source = [p for p in out["points"] if p.strip()], llm.clean_text(out["narrative"]), "ai"
        except llm.AIError:
            pass  # keep the code-only summary
    subject = [s.text for s in segs if s.role == "subject"] or [s.text for s in segs]
    draft.points, draft.narrative, draft.summary_source = points, narrative, source
    draft.narrative_flags = summary.verify_text(narrative, segs)
    draft.keywords = summary.keywords(subject)
    draft.save(update_fields=["points", "narrative", "summary_source", "narrative_flags", "keywords"])


def update_narrative(draft: StatementDraft, user, text: str, reviewed: bool) -> None:
    draft.narrative = (text or "").strip()
    draft.narrative_reviewed = reviewed
    draft.narrative_flags = summary.verify_text(draft.narrative, segs_of(draft.session))
    draft.save(update_fields=["narrative", "narrative_reviewed", "narrative_flags"])
    log(user, "edit", "interview", draft.session_id, f"ตรวจ/แก้คำให้การแบบเล่าเรื่อง ร่างฉบับที่ {draft.version}")


def set_segment_speaker(seg: Segment, user, label: str) -> None:
    if not re.fullmatch(r"S\d{1,2}", label or ""):
        return
    old = seg.speaker
    seg.speaker = label
    seg.save(update_fields=["speaker"])
    SpeakerMap.objects.get_or_create(session=seg.session, label=label, defaults={"role": ""})
    log(user, "edit", "interview_segment", seg.pk, f"เปลี่ยนผู้พูดช่วง {seg.idx}: {old} → {label}")


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
    rights_ok = session.rights.exists() and not session.rights.filter(found_at__isnull=True).exists()
    if session.statement_format == "narrative":
        problems = []
        if not draft or not draft.narrative.strip():
            problems.append("ยังไม่มีคำให้การแบบเล่าเรื่อง")
        elif not draft.narrative_reviewed:
            problems.append('ยังไม่ได้ติ๊ก "ฟังแล้ว ตรงกับเสียง" ในคำให้การแบบเล่าเรื่อง')
        if session.is_suspect and not rights_ok and len((override or session.rights_override or "").strip()) < 10:
            problems.append("ตรวจไม่พบการแจ้งสิทธิครบในเสียง ต้องฟังเองและระบุเหตุผลก่อนยืนยัน")
        return problems
    reviewed = list(draft.pairs.values_list("reviewed", flat=True)) if draft else []
    return qa.confirm_blockers(reviewed, rights_ok, override or session.rights_override, session.is_suspect)


def statement_text(draft: StatementDraft, fmt: str = "qa") -> str:
    if fmt == "narrative":
        return draft.narrative
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
    st = Statement.objects.create(party=cp, version=version, text=statement_text(draft, session.statement_format), source=Statement.Source.VOICE,
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


@transaction.atomic
def quick_start(user, name: str, phone: str, kind: str, title: str, place: str) -> InterviewSession:
    """ผู้แจ้งความรายใหม่ที่มาที่สถานี: สร้างคดี (รับแจ้งใหม่) + ผู้กล่าวหา + ห้องสอบ ในขั้นเดียว."""
    from ai.fake import split_name
    from cases.models import Case, CaseStatus, CaseType
    from deadlines.services import sync_rule_tasks
    from parties.models import CaseParty, Person

    case = Case.objects.create(owner=user, case_type=CaseType.ONLINE if kind == "online" else CaseType.CRIMINAL,
                               status=CaseStatus.RECEIVED, title=title[:300])  # fmt: skip
    n = split_name(name)
    person = Person.objects.create(
        title=n["title"], first_name=n["first_name"] or name[:120], last_name=n["last_name"], phone=phone[:40]
    )
    party = CaseParty.objects.create(case=case, person=person, role="accuser", order=1)
    log(user, "create", "case", case.pk, f"สร้างคดีจากห้องสอบปากคำ (ผู้แจ้งรายใหม่) {person}")
    sync_rule_tasks(case)
    return start(user, party, place, [])
