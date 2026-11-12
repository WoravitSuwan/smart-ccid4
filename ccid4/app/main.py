"""CCID4 web application (FastAPI + server-rendered Thai UI)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import audit, config, documents, importers, thai, transcribe
from .db import SessionLocal, get_db, init_db
from .interview import Seg, build_pairs, check_rights, confirm_blockers, number_flags
from .models import (PERSON_ROLES, ROLES, SPEAKER_ROLES, STAGES, AuditLog, Case, Deadline, Interview,
                     Person, QAPair, RegisterRow, Segment, User, now)
from .security import (LoginRequired, check_csrf, csrf_token, current_user, hash_password, password_problem,
                       require, verify_password)

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="CCID4", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="ccid4",
                   max_age=config.SESSION_HOURS * 3600, same_site="strict", https_only=config.COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals.update(thai=thai, config=config, ROLES=ROLES, STAGES=STAGES, PERSON_ROLES=PERSON_ROLES,
                             SPEAKER_ROLES=SPEAKER_ROLES, asr_available=transcribe.asr_available,
                             pdf_available=documents.pdf_available)

CSRF = Depends(check_csrf)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    if request.headers.get("accept", "").startswith("application/json") or request.url.path.endswith("/chunk"):
        return JSONResponse({"error": "login"}, status_code=401)
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=()")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'")
    return resp


def render(request: Request, name: str, user: User | None = None, **ctx):
    ctx.update(request=request, user=user, csrf=csrf_token(request), now=now(), flash=request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, ctx)


def back(url: str, msg: str | None = None, request: Request | None = None):
    if msg and request is not None:
        request.session["flash"] = msg
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ auth
_fails: dict[str, list[float]] = defaultdict(list)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", next=next if next.startswith("/") else "/")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db), _=CSRF):
    key = email.strip().lower()
    _fails[key] = [t for t in _fails[key] if t > time.time() - 900]
    if len(_fails[key]) >= 5:
        return render(request, "login.html", next=next, error="ลองเข้าสู่ระบบผิดหลายครั้ง รอ 15 นาทีแล้วลองใหม่")
    user = db.execute(select(User).where(User.email == key)).scalar_one_or_none()
    if not user or not user.active or not verify_password(password, user.password_hash):
        _fails[key].append(time.time())
        audit.log(db, user, "login_failed", "user", key, request=request)
        return render(request, "login.html", next=next, error="อีเมลหรือรหัสผ่านไม่ถูกต้อง")
    _fails.pop(key, None)
    request.session.clear()
    request.session.update(uid=user.id, exp=time.time() + config.SESSION_HOURS * 3600)
    audit.log(db, user, "login", "user", user.id, request=request)
    return back(next if next.startswith("/") and not next.startswith("//") else "/")


@app.post("/logout")
def logout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=CSRF):
    audit.log(db, user, "logout", "user", user.id, request=request)
    request.session.clear()
    return back("/login")


# ------------------------------------------------------------------ access helpers
def visible_cases(user: User):
    q = select(Case)
    if user.role == "admin":
        return q.where(Case.id == -1)  # administrators manage the system, not case content
    if user.role == "investigator":
        q = q.where(Case.investigator_id == user.id)
    return q


def get_case(db: Session, user: User, case_id: int) -> Case:
    case = db.execute(visible_cases(user).where(Case.id == case_id)).scalar_one_or_none()
    if not case:
        raise HTTPException(404, "ไม่พบคดี หรือไม่มีสิทธิ์เข้าถึง")
    return case


def get_interview(db: Session, user: User, iid: int) -> Interview:
    iv = db.get(Interview, iid)
    if not iv:
        raise HTTPException(404, "ไม่พบการสอบปากคำ")
    get_case(db, user, iv.case_id)
    return iv


def deadline_state(d: Deadline, ref: datetime | None = None) -> tuple[str, str]:
    ref = ref or now()
    diff = (d.due_at - ref).total_seconds()
    if d.done:
        return "ok", "เสร็จแล้ว"
    if diff < 0:
        return "late", f"เกินกำหนด {int(-diff // 86400) + 1} วัน"
    days, hours = int(diff // 86400), int(diff % 86400 // 3600)
    label = f"อีก {days} วัน {hours} ชม." if days else f"อีก {hours} ชม. {int(diff % 3600 // 60)} นาที"
    return ("due" if diff <= 3 * 86400 else "inf"), label


templates.env.globals["deadline_state"] = deadline_state


# ------------------------------------------------------------------ dashboard & cases
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        return back("/admin/users")
    case_ids = select(visible_cases(user).subquery().c.id)
    soon = db.execute(select(Deadline).where(Deadline.case_id.in_(case_ids), Deadline.done.is_(False),
                                             Deadline.due_at <= now() + timedelta(days=7))
                      .order_by(Deadline.due_at)).scalars().all()
    waiting = db.execute(select(Interview).where(Interview.case_id.in_(case_ids),
                                                 Interview.status.in_(["recorded", "transcribed", "drafted"]))
                         .order_by(Interview.id.desc())).scalars().all()
    n_cases = len(db.execute(visible_cases(user).where(Case.stage != "ส่งอัยการ")).scalars().all())
    workload = []
    if user.role == "chief":
        for inv in db.execute(select(User).where(User.role == "investigator", User.active)).scalars():
            cs = db.execute(select(Case).where(Case.investigator_id == inv.id, Case.stage != "ส่งอัยการ")).scalars().all()
            due = sum(1 for c in cs for d in c.deadlines if not d.done and d.due_at <= now() + timedelta(days=3))
            workload.append((inv, len(cs), due))
        workload.sort(key=lambda x: -x[1])
    return render(request, "dashboard.html", user, soon=soon, waiting=waiting, n_cases=n_cases,
                  within3=[d for d in soon if d.due_at <= now() + timedelta(days=3)], workload=workload,
                  max_load=max([w[1] for w in workload] or [1]))


@app.get("/cases", response_class=HTMLResponse)
def cases(request: Request, q: str = "", f: str = "all", user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = visible_cases(user).order_by(Case.id.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Case.case_no.like(like), Case.receipt_no.like(like), Case.charge.like(like),
                              Case.persons.any(Person.name.like(like))))
    if f in ("online", "general"):
        stmt = stmt.where(Case.case_type == f)
    rows = db.execute(stmt).scalars().all()
    if f == "due":
        rows = [c for c in rows if any(not d.done and d.due_at <= now() + timedelta(days=3) for d in c.deadlines)]
    return render(request, "cases.html", user, rows=rows, q=q, f=f)


@app.get("/cases/new", response_class=HTMLResponse)
def case_new(request: Request, user: User = Depends(require("edit_case")), db: Session = Depends(get_db)):
    invs = db.execute(select(User).where(User.role == "investigator", User.active)).scalars().all()
    return render(request, "case_new.html", user, investigators=invs)


@app.post("/cases")
def case_create(request: Request, case_no: str = Form(...), charge: str = Form(""), case_type: str = Form("online"),
                receipt_no: str = Form(""), complainant: str = Form(""), summary: str = Form(""),
                investigator_id: int | None = Form(None),
                user: User = Depends(require("edit_case")), db: Session = Depends(get_db), _=CSRF):
    inv_id = user.id if user.role == "investigator" else investigator_id
    c = Case(case_no=case_no.strip(), charge=charge.strip(), case_type=case_type, receipt_no=receipt_no.strip(),
             summary=summary.strip(), investigator_id=inv_id, stage="รับเรื่อง")
    db.add(c)
    db.flush()
    if complainant.strip():
        db.add(Person(case_id=c.id, role="ผู้กล่าวหา", name=complainant.strip()))
    audit.log(db, user, "create", "case", c.id, f"สร้างคดี {c.case_no}", request)
    return back(f"/cases/{c.id}", "สร้างคดีแล้ว", request)


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: int, tab: str = "people", user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    c = get_case(db, user, case_id)
    audit.log(db, user, "view", "case", c.id, request=request)
    logs = db.execute(select(AuditLog).where(AuditLog.object_type == "case", AuditLog.object_id == str(c.id),
                                             AuditLog.action != "view").order_by(AuditLog.id.desc()).limit(12)).scalars().all()
    return render(request, "case.html", user, c=c, tab=tab, logs=logs)


@app.post("/cases/{case_id}/edit")
def case_edit(request: Request, case_id: int, charge: str = Form(""), stage: str = Form(...), status: str = Form(""),
              summary: str = Form(""), damage_amount: str = Form("0"),
              user: User = Depends(require("edit_case")), db: Session = Depends(get_db), _=CSRF):
    c = get_case(db, user, case_id)
    c.charge, c.stage, c.status, c.summary = charge.strip(), stage, status.strip(), summary.strip()
    try:
        c.damage_amount = float(damage_amount.replace(",", "") or 0)
    except ValueError:
        pass
    audit.log(db, user, "edit", "case", c.id, "แก้ข้อมูลคดี", request)
    return back(f"/cases/{c.id}?tab=info", "บันทึกแล้ว", request)


@app.post("/cases/{case_id}/persons")
def person_add(request: Request, case_id: int, role: str = Form(...), name: str = Form(...), citizen_id: str = Form(""),
               phone: str = Form(""), address: str = Form(""), postcode: str = Form(""),
               user: User = Depends(require("edit_case")), db: Session = Depends(get_db), _=CSRF):
    c = get_case(db, user, case_id)
    p = Person(case_id=c.id, role=role, name=name.strip(), citizen_id=citizen_id.strip(), phone=phone.strip(),
               address=address.strip(), postcode=postcode.strip())
    db.add(p)
    audit.log(db, user, "create", "case", c.id, f"เพิ่ม{role} {p.name}", request)
    return back(f"/cases/{c.id}?tab=people", "เพิ่มบุคคลแล้ว", request)


@app.post("/cases/{case_id}/deadlines")
def deadline_add(request: Request, case_id: int, kind: str = Form(...), title: str = Form(...), due_date: str = Form(...),
                 due_time: str = Form("16:30"), basis: str = Form(""),
                 user: User = Depends(require("edit_case")), db: Session = Depends(get_db), _=CSRF):
    c = get_case(db, user, case_id)
    try:
        d = thai.parse_be_date(due_date)
        hh, mm = map(int, due_time.split(":"))
    except ValueError as e:
        return back(f"/cases/{c.id}?tab=time", str(e), request)
    db.add(Deadline(case_id=c.id, kind=kind, title=title.strip(), due_at=datetime(d.year, d.month, d.day, hh, mm),
                    basis=basis.strip()))
    audit.log(db, user, "create", "case", c.id, f"เพิ่มกำหนดเวลา {title}", request)
    return back(f"/cases/{c.id}?tab=time", "เพิ่มกำหนดเวลาแล้ว", request)


@app.post("/deadlines/{did}/{action}")
def deadline_action(request: Request, did: int, action: str, user: User = Depends(require("edit_case")),
                    db: Session = Depends(get_db), _=CSRF):
    d = db.get(Deadline, did)
    if not d:
        raise HTTPException(404)
    get_case(db, user, d.case_id)
    if action == "confirm":
        d.confirmed = True
    elif action == "done":
        d.done = not d.done
    else:
        raise HTTPException(400)
    audit.log(db, user, "edit", "case", d.case_id, f"กำหนดเวลา: {d.title} ({action})", request)
    return back(f"/cases/{d.case_id}?tab=time")


@app.get("/deadlines", response_class=HTMLResponse)
def deadlines(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = select(visible_cases(user).subquery().c.id)
    rows = db.execute(select(Deadline).where(Deadline.case_id.in_(ids), Deadline.done.is_(False))
                      .order_by(Deadline.due_at)).scalars().all()
    return render(request, "deadlines.html", user, rows=rows)


# ------------------------------------------------------------------ interview room
CHECKLIST = [
    ("identity", "ตรวจสอบตัวตนผู้ให้ถ้อยคำกับบัตรประชาชน"),
    ("charge", "แจ้งข้อกล่าวหาให้ทราบ (กรณีผู้ต้องหา)"),
    ("silence", "แจ้งสิทธิให้การหรือไม่ให้การก็ได้ และถ้อยคำอาจใช้เป็นพยานหลักฐาน"),
    ("counsel", "แจ้งสิทธิมีทนายความหรือผู้ไว้วางใจเข้าฟัง"),
    ("recording", "แจ้งว่ามีการบันทึกเสียง และสอบถามความต้องการล่าม"),
]


@app.post("/cases/{case_id}/interviews")
def interview_create(request: Request, case_id: int, person_id: int = Form(...),
                     user: User = Depends(require("listen_audio")), db: Session = Depends(get_db), _=CSRF):
    c = get_case(db, user, case_id)
    p = db.get(Person, person_id)
    if not p or p.case_id != c.id:
        raise HTTPException(400, "เลือกบุคคลในคดีนี้")
    iv = Interview(case_id=c.id, person_id=p.id, title=f"สอบปากคำ{p.role} {p.name}", created_by_id=user.id)
    db.add(iv)
    db.flush()
    audit.log(db, user, "create", "case", c.id, f"เริ่ม{iv.title}", request)
    return back(f"/interviews/{iv.id}/prepare")


@app.get("/interviews/{iid}/prepare", response_class=HTMLResponse)
def interview_prepare(request: Request, iid: int, user: User = Depends(require("listen_audio")), db: Session = Depends(get_db)):
    iv = get_interview(db, user, iid)
    return render(request, "prepare.html", user, iv=iv, checklist=CHECKLIST, checked=json.loads(iv.checklist_json or "{}"))


@app.post("/interviews/{iid}/prepare")
async def interview_prepare_save(request: Request, iid: int, user: User = Depends(require("listen_audio")),
                                 db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    form = await request.form()
    checked = {k: (form.get(k) == "on") for k, _ in CHECKLIST}
    iv.checklist_json = json.dumps(checked)
    is_suspect = iv.person and iv.person.role == "ผู้ต้องหา"
    required = [k for k, _ in CHECKLIST if is_suspect or k not in ("charge", "silence", "counsel")]
    if not all(checked[k] for k in required):
        db.commit()
        return back(f"/interviews/{iid}/prepare", "ติ๊กเช็กลิสต์ให้ครบก่อนเริ่มบันทึก", request)
    iv.status = "recording"
    iv.started_at = now()
    audit.log(db, user, "record", "interview", iv.id, "ยืนยันเช็กลิสต์และเริ่มบันทึกเสียง", request)
    return back(f"/interviews/{iid}/record")


@app.get("/interviews/{iid}/record", response_class=HTMLResponse)
def interview_record(request: Request, iid: int, user: User = Depends(require("listen_audio")), db: Session = Depends(get_db)):
    iv = get_interview(db, user, iid)
    if iv.status not in ("recording",):
        return back(f"/interviews/{iid}")
    return render(request, "record.html", user, iv=iv)


@app.post("/interviews/{iid}/chunk")
async def interview_chunk(request: Request, iid: int, user: User = Depends(require("listen_audio")),
                          db: Session = Depends(get_db), _=CSRF):
    """Append one MediaRecorder chunk. Chunks must arrive in order (x-seq)."""
    iv = get_interview(db, user, iid)
    if iv.status != "recording":
        raise HTTPException(409, "การบันทึกปิดแล้ว")
    seq = int(request.headers.get("x-seq", "-1"))
    elapsed = float(request.headers.get("x-elapsed", "0"))
    mime = request.headers.get("content-type", "audio/webm").split(";")[0]
    body = await request.body()
    if not iv.audio_path:
        ext = ".ogg" if "ogg" in mime else ".m4a" if "mp4" in mime else ".webm"
        iv.audio_path = str(config.AUDIO_DIR / f"interview-{iv.id}-{uuid.uuid4().hex[:8]}{ext}")
        iv.audio_mime = mime
    path = Path(iv.audio_path)
    meta_path = path.with_suffix(path.suffix + ".seq")
    last = int(meta_path.read_text()) if meta_path.exists() else -1
    if seq <= last:  # duplicate retry
        return {"uploaded_until": iv.uploaded_until_sec, "seq": last}
    if seq != last + 1:
        raise HTTPException(409, f"ข้อมูลเสียงขาดช่วง (ได้รับ {seq} ต้องการ {last + 1})")
    with path.open("ab") as fh:
        fh.write(body)
    meta_path.write_text(str(seq))
    iv.audio_bytes = path.stat().st_size
    iv.uploaded_until_sec = max(iv.uploaded_until_sec, elapsed)
    db.commit()
    return {"uploaded_until": iv.uploaded_until_sec, "seq": seq}


@app.post("/interviews/{iid}/mark")
def interview_mark(request: Request, iid: int, t: float = Form(...), note: str = Form("เครื่องหมาย"),
                   user: User = Depends(require("listen_audio")), db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    marks = json.loads(iv.marks_json or "[]")
    marks.append({"t": t, "note": note[:120]})
    iv.marks_json = json.dumps(marks, ensure_ascii=False)
    db.commit()
    return {"marks": marks}


def _finalize_audio(db: Session, iv: Interview) -> None:
    path = Path(iv.audio_path)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    iv.sha256 = h.hexdigest()
    iv.duration_sec = transcribe.audio_duration(path) or iv.uploaded_until_sec
    iv.ended_at = now()
    iv.status = "recorded"
    try:
        path.chmod(0o440)  # keep the original read-only
    except OSError:
        pass


def _run_asr(iid: int) -> None:
    db = SessionLocal()
    try:
        iv = db.get(Interview, iid)
        segs = transcribe.transcribe(Path(iv.audio_path))
        _replace_segments(db, iv, segs)
        audit.log(db, None, "edit", "interview", iv.id, f"ถอดเสียงในเครื่องเสร็จ {len(segs)} ช่วง")
    except Exception as e:  # pragma: no cover - depends on local model
        audit.log(db, None, "edit", "interview", iid, f"ถอดเสียงไม่สำเร็จ: {e}")
    finally:
        db.close()


def _replace_segments(db: Session, iv: Interview, segs: list[dict]) -> None:
    for s in list(iv.segments):
        db.delete(s)
    db.flush()
    first = segs[0].get("speaker", "S1") if segs else "S1"
    for s in segs:
        spk = str(s.get("speaker") or "S1")
        role = "investigator" if spk == first else "subject"
        db.add(Segment(interview_id=iv.id, start=s["start"], end=s["end"], speaker=spk, role=role, text=s["text"]))
    iv.status = "transcribed"
    db.commit()


@app.post("/interviews/{iid}/finish")
def interview_finish(request: Request, iid: int, user: User = Depends(require("listen_audio")),
                     db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    if not iv.audio_path or not Path(iv.audio_path).exists():
        raise HTTPException(400, "ยังไม่มีเสียงที่บันทึก")
    _finalize_audio(db, iv)
    audit.log(db, user, "record", "interview", iv.id, f"จบการบันทึก SHA-256 {iv.sha256}", request)
    if transcribe.asr_available():
        threading.Thread(target=_run_asr, args=(iv.id,), daemon=True).start()
    return {"redirect": f"/interviews/{iv.id}"}


@app.post("/interviews/{iid}/upload-audio")
async def interview_upload_audio(request: Request, iid: int, file: UploadFile = File(...),
                                 user: User = Depends(require("listen_audio")), db: Session = Depends(get_db), _=CSRF):
    """For recordings made on a separate recorder."""
    iv = get_interview(db, user, iid)
    if iv.sha256:
        raise HTTPException(409, "การสอบปากคำนี้มีไฟล์เสียงต้นฉบับแล้ว")
    ext = Path(file.filename or "audio").suffix.lower()[:6] or ".bin"
    path = config.AUDIO_DIR / f"interview-{iv.id}-{uuid.uuid4().hex[:8]}{ext}"
    with path.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    iv.audio_path, iv.audio_mime, iv.started_at = str(path), file.content_type or "", iv.started_at or now()
    iv.audio_bytes = path.stat().st_size
    _finalize_audio(db, iv)
    audit.log(db, user, "record", "interview", iv.id, f"อัปโหลดไฟล์เสียง {file.filename} SHA-256 {iv.sha256}", request)
    if transcribe.asr_available():
        threading.Thread(target=_run_asr, args=(iv.id,), daemon=True).start()
    return back(f"/interviews/{iv.id}", "เก็บไฟล์เสียงต้นฉบับแล้ว", request)


@app.get("/interviews/{iid}/audio")
def interview_audio(request: Request, iid: int, user: User = Depends(require("listen_audio")), db: Session = Depends(get_db)):
    iv = get_interview(db, user, iid)
    if not iv.audio_path or not Path(iv.audio_path).exists():
        raise HTTPException(404)
    if "range" not in request.headers or request.headers.get("range", "").startswith("bytes=0-"):
        audit.log(db, user, "listen", "interview", iv.id, "เปิดฟังเสียง", request)
    return FileResponse(iv.audio_path, media_type=iv.audio_mime or "audio/webm")


def _segs(iv: Interview) -> list[Seg]:
    return [Seg(s.start, s.end, s.role, s.text) for s in iv.segments]


@app.get("/interviews/{iid}", response_class=HTMLResponse)
def interview_review(request: Request, iid: int, user: User = Depends(require("listen_audio")), db: Session = Depends(get_db)):
    iv = get_interview(db, user, iid)
    segs = _segs(iv)
    rights = check_rights(segs)
    flags = {p.id: number_flags(p.answer, p.question, segs, p.start, p.end) for p in iv.pairs}
    is_suspect = bool(iv.person and iv.person.role == "ผู้ต้องหา")
    blockers = confirm_blockers([p.reviewed for p in iv.pairs], rights, iv.rights_manual_confirmed, is_suspect)
    if not user.can("confirm_statement"):
        blockers.append("เฉพาะพนักงานสอบสวนยืนยันคำให้การได้")
    speakers = sorted({s.speaker for s in iv.segments})
    speaker_roles = {s.speaker: s.role for s in iv.segments}
    audit.log(db, user, "view", "interview", iv.id, request=request)
    return render(request, "review.html", user, iv=iv, rights=rights, flags=flags, blockers=blockers,
                  is_suspect=is_suspect, speakers=speakers, speaker_roles=speaker_roles,
                  marks=json.loads(iv.marks_json or "[]"))


def _editable(iv: Interview):
    if iv.status == "confirmed":
        raise HTTPException(409, "ยืนยันคำให้การแล้ว แก้ไขไม่ได้")


@app.post("/interviews/{iid}/transcript")
def interview_transcript(request: Request, iid: int, text: str = Form(...), user: User = Depends(require("listen_audio")),
                         db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    segs = transcribe.parse_transcript(text)
    if not segs:
        return back(f"/interviews/{iid}", "อ่านข้อความถอดเสียงไม่ได้ ดูรูปแบบตัวอย่างในหน้า", request)
    _replace_segments(db, iv, segs)
    audit.log(db, user, "edit", "interview", iv.id, f"นำเข้าข้อความถอดเสียง {len(segs)} ช่วง", request)
    return back(f"/interviews/{iid}", f"นำเข้าข้อความ {len(segs)} ช่วงแล้ว ตรวจบทบาทผู้พูดก่อนสร้างร่าง", request)


@app.post("/interviews/{iid}/speakers")
async def interview_speakers(request: Request, iid: int, user: User = Depends(require("listen_audio")),
                             db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    form = await request.form()
    for s in iv.segments:
        role = form.get(f"role_{s.speaker}")
        if role in SPEAKER_ROLES:
            s.role = role
    audit.log(db, user, "edit", "interview", iv.id, "กำหนดบทบาทผู้พูด", request)
    return back(f"/interviews/{iid}", "บันทึกบทบาทผู้พูดแล้ว", request)


@app.post("/interviews/{iid}/segments/{sid}")
def interview_segment_edit(request: Request, iid: int, sid: int, text: str = Form(...), role: str = Form(...),
                           user: User = Depends(require("listen_audio")), db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    s = db.get(Segment, sid)
    if not s or s.interview_id != iv.id or role not in SPEAKER_ROLES:
        raise HTTPException(400)
    old = s.text
    s.text, s.role = text.strip(), role
    audit.log(db, user, "edit", "interview", iv.id, f"แก้ข้อความถอดเสียง {thai.mmss(s.start)}: «{old}» → «{s.text}»", request)
    return back(f"/interviews/{iid}#seg-{sid}")


@app.post("/interviews/{iid}/draft")
def interview_draft(request: Request, iid: int, user: User = Depends(require("listen_audio")),
                    db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    pairs = build_pairs(_segs(iv))
    for p in list(iv.pairs):
        db.delete(p)
    db.flush()
    for i, p in enumerate(pairs):
        db.add(QAPair(interview_id=iv.id, idx=i, question=p.question, answer=p.answer, start=p.start, end=p.end))
    iv.status = "drafted"
    audit.log(db, user, "edit", "interview", iv.id, f"สร้างร่างถาม-ตอบ {len(pairs)} คู่", request)
    return back(f"/interviews/{iid}", f"สร้างร่าง {len(pairs)} คู่แล้ว ตรวจทุกคู่กับเสียง", request)


@app.post("/interviews/{iid}/pairs/{pid}")
def interview_pair_edit(request: Request, iid: int, pid: int, question: str = Form(...), answer: str = Form(...),
                        reviewed: str = Form(""), user: User = Depends(require("listen_audio")),
                        db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    p = db.get(QAPair, pid)
    if not p or p.interview_id != iv.id:
        raise HTTPException(404)
    changed = (p.question, p.answer) != (question.strip(), answer.strip())
    p.question, p.answer, p.reviewed = question.strip(), answer.strip(), reviewed == "on"
    audit.log(db, user, "edit", "interview", iv.id,
              f"คู่ที่ {p.idx + 1}: {'แก้ข้อความและ' if changed else ''}{'ตรวจแล้ว' if p.reviewed else 'ยังไม่ตรวจ'}", request)
    return back(f"/interviews/{iid}#pair-{pid}")


@app.post("/interviews/{iid}/rights-manual")
def interview_rights_manual(request: Request, iid: int, ok: str = Form(""), user: User = Depends(require("confirm_statement")),
                            db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    iv.rights_manual_confirmed = ok == "on"
    audit.log(db, user, "confirm", "interview", iv.id,
              "ยืนยันด้วยตนเองว่าได้แจ้งสิทธิครบ" if iv.rights_manual_confirmed else "ยกเลิกการยืนยันการแจ้งสิทธิ", request)
    return back(f"/interviews/{iid}#confirm")


@app.post("/interviews/{iid}/confirm")
def interview_confirm(request: Request, iid: int, user: User = Depends(require("confirm_statement")),
                      db: Session = Depends(get_db), _=CSRF):
    iv = get_interview(db, user, iid)
    _editable(iv)
    is_suspect = bool(iv.person and iv.person.role == "ผู้ต้องหา")
    blockers = confirm_blockers([p.reviewed for p in iv.pairs], check_rights(_segs(iv)), iv.rights_manual_confirmed, is_suspect)
    if blockers:
        return back(f"/interviews/{iid}#confirm", " · ".join(blockers), request)
    iv.status, iv.confirmed_by_id, iv.confirmed_at = "confirmed", user.id, now()
    audit.log(db, user, "confirm", "interview", iv.id, f"ยืนยันคำให้การ {len(iv.pairs)} คู่", request)
    return back(f"/interviews/{iid}", "ยืนยันคำให้การแล้ว", request)


@app.get("/interviews/{iid}/export.{fmt}")
def interview_export(request: Request, iid: int, fmt: str, times: int = 1, user: User = Depends(require("listen_audio")),
                     db: Session = Depends(get_db)):
    iv = get_interview(db, user, iid)
    if fmt not in ("docx", "pdf"):
        raise HTTPException(404)
    c, inv = iv.case, iv.case.investigator
    data = documents.StatementData(
        person_role=iv.person.role if iv.person else "ผู้ให้ถ้อยคำ", person_name=iv.person.name if iv.person else "",
        case_no=c.case_no, charge=c.charge, place=config.UNIT_SHORT, when=iv.started_at or iv.created_at,
        investigator=inv.full_name if inv else "", investigator_position=inv.position if inv else "",
        pairs=[(p.question, p.answer, p.start, p.end) for p in iv.pairs], audio_sha256=iv.sha256,
        audio_duration=iv.duration_sec, include_times=bool(times), draft=iv.status != "confirmed")
    name = f"{c.case_no.replace('/', '-')} คำให้การ{data.person_role} {data.person_name}{' (ร่าง)' if data.draft else ''}"
    out = documents.statement_docx(data, config.OUTPUT_DIR / f"{uuid.uuid4().hex}" / f"{name}.docx")
    if fmt == "pdf":
        try:
            out = documents.to_pdf(out)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
    audit.log(db, user, "download", "interview", iv.id, f"ส่งออก {out.name}", request)
    return FileResponse(out, filename=out.name)


# ------------------------------------------------------------------ batch summons
@app.get("/summons", response_class=HTMLResponse)
def summons_page(request: Request, imp: str = "", user: User = Depends(require("summons")), db: Session = Depends(get_db)):
    rows = []
    if imp:
        rows = db.execute(select(RegisterRow).where(RegisterRow.import_id == imp).order_by(RegisterRow.row_no)).scalars().all()
    open_rows = [r for r in rows if r.status not in config.CLOSED_STATUSES]
    default_date = date.today() + timedelta(days=14)
    return render(request, "summons.html", user, imp=imp, rows=open_rows, total=len(rows),
                  default_date=f"{default_date.day:02d}/{default_date.month:02d}/{thai.to_be(default_date.year)}")


@app.post("/summons/import")
async def summons_import(request: Request, file: UploadFile = File(...), user: User = Depends(require("summons")),
                         db: Session = Depends(get_db), _=CSRF):
    data = await file.read()
    try:
        parsed = importers.read_register(data, file.filename or "")
    except ValueError as e:
        return back("/summons", str(e), request)
    imp = uuid.uuid4().hex[:12]
    for r in parsed:
        db.add(RegisterRow(import_id=imp, **r))
    audit.log(db, user, "import", "register", imp, f"นำเข้าทะเบียน {file.filename} {len(parsed)} แถว", request)
    return back(f"/summons?imp={imp}", f"นำเข้า {len(parsed)} แถวแล้ว", request)


@app.post("/summons/generate")
async def summons_generate(request: Request, user: User = Depends(require("summons")), db: Session = Depends(get_db), _=CSRF):
    form = await request.form()
    ids = [int(x) for x in form.getlist("row")]
    imp = str(form.get("imp", ""))
    rows = db.execute(select(RegisterRow).where(RegisterRow.id.in_(ids), RegisterRow.import_id == imp)).scalars().all()
    rows = [r for r in rows if not r.problem]
    if not rows:
        return back(f"/summons?imp={imp}", "เลือกอย่างน้อย 1 รายการที่ที่อยู่ครบ", request)
    try:
        appt = thai.parse_be_date(str(form.get("date", "")))
        start = str(form.get("time", "09:00"))
        thai.add_minutes(start, 0)
    except ValueError as e:
        return back(f"/summons?imp={imp}", str(e), request)
    stagger = form.get("stagger") == "on"
    place = str(form.get("place") or config.UNIT_SHORT)
    per_inv: dict[str, int] = defaultdict(int)
    items = []
    for r in sorted(rows, key=lambda r: (r.investigator, r.row_no)):
        n = per_inv[r.investigator]
        per_inv[r.investigator] += 1
        items.append(documents.SummonsItem(
            case_no=r.case_no, charge=r.charge, name=r.complainant, address=r.address, postcode=r.postcode,
            appointment=appt, time=thai.add_minutes(start, 30 * n) if stagger else start, place=place,
            signer=r.investigator or user.full_name, signer_position="พนักงานสอบสวน", signer_phone=user.phone))
    folder = config.OUTPUT_DIR / uuid.uuid4().hex
    files = [documents.summons_docx(items, date.today(), folder / f"หมายเรียก {len(items)} ฉบับ.docx")]
    if form.get("envelopes") == "on":
        files.append(documents.envelopes_docx(items, folder / f"ใบจ่าหน้าซอง {len(items)} ฉบับ.docx"))
    if form.get("pdf") == "on":
        try:
            files += [documents.to_pdf(f) for f in list(files)]
        except RuntimeError as e:
            request.session["flash"] = str(e)
    audit.log(db, user, "export", "register", imp, f"ออกหมายเรียก {len(items)} ฉบับ นัด {thai.format_date(appt)}", request)
    links = [f"/files/{folder.name}/{f.name}" for f in files]
    return render(request, "summons_done.html", user, links=links, count=len(items), appt=appt)


@app.get("/files/{folder}/{name}")
def download(request: Request, folder: str, name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    path = (config.OUTPUT_DIR / folder / name).resolve()
    if config.OUTPUT_DIR not in path.parents or not path.exists():
        raise HTTPException(404)
    audit.log(db, user, "download", "file", folder, name, request)
    return FileResponse(path, filename=name)


# ------------------------------------------------------------------ audit & admin
@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, who: str = "", action: str = "", user: User = Depends(require("view_audit")),
               db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(300)
    if who:
        stmt = stmt.where(AuditLog.user_name.like(f"%{who}%"))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    ok, bad = audit.verify_chain(db)
    return render(request, "audit.html", user, rows=db.execute(stmt).scalars().all(), ok=ok, bad=bad,
                  actions=audit.ACTIONS, who=who, action=action)


@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: User = Depends(require("manage_users")), db: Session = Depends(get_db)):
    return render(request, "users.html", user, rows=db.execute(select(User).order_by(User.id)).scalars().all())


@app.post("/admin/users")
def users_create(request: Request, email: str = Form(...), full_name: str = Form(...), position: str = Form(""),
                 phone: str = Form(""), role: str = Form(...), password: str = Form(...),
                 user: User = Depends(require("manage_users")), db: Session = Depends(get_db), _=CSRF):
    email = email.strip().lower()
    if role not in ROLES:
        raise HTTPException(400)
    if problem := password_problem(password):
        return back("/admin/users", problem, request)
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        return back("/admin/users", "มีอีเมลนี้ในระบบแล้ว", request)
    u = User(email=email, full_name=full_name.strip(), position=position.strip(), phone=phone.strip(), role=role,
             password_hash=hash_password(password))
    db.add(u)
    db.flush()
    audit.log(db, user, "create", "user", u.id, f"สร้างผู้ใช้ {email} ({ROLES[role]})", request)
    return back("/admin/users", "สร้างผู้ใช้แล้ว", request)


@app.post("/admin/users/{uid}/toggle")
def users_toggle(request: Request, uid: int, user: User = Depends(require("manage_users")), db: Session = Depends(get_db), _=CSRF):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    if u.id == user.id:
        return back("/admin/users", "ปิดบัญชีของตนเองไม่ได้", request)
    u.active = not u.active
    audit.log(db, user, "edit", "user", u.id, "เปิดใช้งาน" if u.active else "ปิดใช้งาน", request)
    return back("/admin/users")


@app.get("/healthz")
def healthz():
    return {"ok": True, "asr": transcribe.asr_available(), "pdf": documents.pdf_available()}
