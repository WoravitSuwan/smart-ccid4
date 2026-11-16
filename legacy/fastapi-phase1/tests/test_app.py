import io
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import audit
from app.db import SessionLocal, init_db
from app.main import app
from app.models import AuditLog, User
from app.security import hash_password

init_db()


def _user(email, role, name):
    db = SessionLocal()
    u = User(email=email, full_name=name, role=role, position="สารวัตร", phone="00 0000 0001",
             password_hash=hash_password("pass-word-123"))
    db.add(u)
    db.commit()
    return u.id


INV = _user("inv@test.invalid", "investigator", "พ.ต.ท.ทดสอบ หนึ่ง")
INV2 = _user("inv2@test.invalid", "investigator", "ร.ต.อ.ทดสอบ สอง")
ADMIN = _user("admin@test.invalid", "admin", "ผู้ดูแล")
CLERK = _user("clerk@test.invalid", "clerk", "ธุรการ")


def client_for(email):
    c = TestClient(app)
    page = c.get("/login").text
    tok = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = c.post("/login", data={"email": email, "password": "pass-word-123", "csrf": tok, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    c.csrf = re.search(r'name="csrf" content="([^"]+)"', c.get("/cases").text).group(1) if email != "admin@test.invalid" else \
        re.search(r'name="csrf" content="([^"]+)"', c.get("/admin/users").text).group(1)
    return c


def post(c, url, data=None, **kw):
    data = dict(data or {})
    data["csrf"] = c.csrf
    return c.post(url, data=data, follow_redirects=False, **kw)


def test_login_required_and_bad_password():
    c = TestClient(app)
    assert c.get("/cases", follow_redirects=False).status_code == 303
    tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)
    r = c.post("/login", data={"email": "inv@test.invalid", "password": "wrong", "csrf": tok})
    assert "ไม่ถูกต้อง" in r.text


def test_csrf_required():
    c = client_for("inv@test.invalid")
    r = c.post("/cases", data={"case_no": "x"}, follow_redirects=False)
    assert r.status_code == 403


@pytest.fixture(scope="module")
def case_flow():
    c = client_for("inv@test.invalid")
    r = post(c, "/cases", {"case_no": "0112/2569", "charge": "ฉ้อโกงประชาชน", "case_type": "online", "complainant": "นายสมมติ ตัวอย่าง"})
    case_id = int(r.headers["location"].split("/")[-1])
    post(c, f"/cases/{case_id}/persons", {"role": "ผู้ต้องหา", "name": "นายทดลอง ไม่มีจริง"})
    page = c.get(f"/cases/{case_id}").text
    pid = re.findall(r'name="person_id" value="(\d+)"', page)[-1]
    r = post(c, f"/cases/{case_id}/interviews", {"person_id": pid})
    iid = int(r.headers["location"].split("/")[2])
    return c, case_id, iid


def test_case_visibility_between_investigators(case_flow):
    c, case_id, _ = case_flow
    other = client_for("inv2@test.invalid")
    assert other.get(f"/cases/{case_id}").status_code == 404
    admin = client_for("admin@test.invalid")
    assert admin.get(f"/cases/{case_id}").status_code == 404


def test_checklist_must_be_complete(case_flow):
    c, _, iid = case_flow
    r = post(c, f"/interviews/{iid}/prepare", {"identity": "on"})
    assert r.headers["location"].endswith("/prepare")
    r = post(c, f"/interviews/{iid}/prepare", {k: "on" for k in ["identity", "charge", "silence", "counsel", "recording"]})
    assert r.headers["location"].endswith("/record")


def _webm(seconds=3) -> bytes:
    out = Path(SessionLocal().bind.url.database).parent / "tone.webm"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "libopus", str(out)], check=True)
    return out.read_bytes()


def test_chunked_recording_and_hash(case_flow):
    c, _, iid = case_flow
    data = _webm()
    half = len(data) // 2
    h = {"content-type": "audio/webm", "x-csrf-token": c.csrf}
    assert c.post(f"/interviews/{iid}/chunk", content=data[:half], headers={**h, "x-seq": "0", "x-elapsed": "1.5"}).json()["seq"] == 0
    # out of order chunk is refused, retry of an old chunk is ignored
    assert c.post(f"/interviews/{iid}/chunk", content=b"x", headers={**h, "x-seq": "5", "x-elapsed": "9"}).status_code == 409
    assert c.post(f"/interviews/{iid}/chunk", content=data[:half], headers={**h, "x-seq": "0", "x-elapsed": "1.5"}).json()["seq"] == 0
    assert c.post(f"/interviews/{iid}/chunk", content=data[half:], headers={**h, "x-seq": "1", "x-elapsed": "3"}).status_code == 200
    r = c.post(f"/interviews/{iid}/finish", headers={"x-csrf-token": c.csrf})
    assert r.json()["redirect"] == f"/interviews/{iid}"
    page = c.get(f"/interviews/{iid}").text
    import hashlib
    assert hashlib.sha256(data).hexdigest()[:8] in page
    assert c.get(f"/interviews/{iid}/audio").content == data


TRANSCRIPT = """[00:31] S1: ท่านถูกกล่าวหาว่าฉ้อโกงประชาชน
[00:45] S1: ท่านมีสิทธิที่จะให้การหรือไม่ให้การก็ได้ ถ้าให้การ ถ้อยคำนั้นอาจใช้เป็นพยานหลักฐาน
[01:04] S2: ครับ เข้าใจครับ
[13:40] S1: มีคนอื่นใช้บัญชีนี้ไหมครับ
[13:48] S2: ผมให้สมุดกับบัตรเอทีเอ็ม เขาบอกว่าจะให้ค่าเช่าสามพันบาทครับ
[14:31] S1: ได้รับเงินจริงไหม
[14:36] S2: ได้ครับ โอนมาครั้งเดียว
"""


def test_review_draft_flags_and_confirm(case_flow):
    c, _, iid = case_flow
    post(c, f"/interviews/{iid}/transcript", {"text": TRANSCRIPT})
    post(c, f"/interviews/{iid}/draft")
    page = c.get(f"/interviews/{iid}").text
    assert "ข้าฯ ให้สมุดกับบัตรเอทีเอ็ม" in page and "▶ 13:40" in page
    assert "✗ สิทธิมีทนายความหรือผู้ไว้วางใจเข้าฟัง" in page
    pair_ids = re.findall(r'id="pair-(\d+)"', page)
    # investigator edits an answer to a figure not heard -> flag appears
    post(c, f"/interviews/{iid}/pairs/{pair_ids[1]}", {"question": "มีคนอื่นใช้บัญชีนี้ไหม",
                                                        "answer": "ข้าฯ ให้สมุดกับบัตรเอทีเอ็ม ค่าเช่า 5,000 บาท", "reviewed": "on"})
    assert "ตัวเลข 5,000 ไม่พบในเสียงช่วงนี้ (ได้ยิน 3,000)" in c.get(f"/interviews/{iid}").text
    # cannot confirm: unreviewed pairs and missing right
    r = post(c, f"/interviews/{iid}/confirm")
    assert "confirm" in r.headers["location"]
    for pid in pair_ids:
        p = c.get(f"/interviews/{iid}").text
        q = re.search(rf'id="pair-{pid}".*?name="question"[^>]*>(.*?)</textarea>.*?name="answer"[^>]*>(.*?)</textarea>', p, re.S)
        post(c, f"/interviews/{iid}/pairs/{pid}", {"question": q.group(1), "answer": q.group(2), "reviewed": "on"})
    assert "ตรวจไม่พบการแจ้งสิทธิ" in c.get(f"/interviews/{iid}").text
    post(c, f"/interviews/{iid}/rights-manual", {"ok": "on"})
    post(c, f"/interviews/{iid}/confirm")
    page = c.get(f"/interviews/{iid}").text
    assert "ยืนยันแล้วโดย" in page
    assert post(c, f"/interviews/{iid}/draft").status_code == 409
    r = c.get(f"/interviews/{iid}/export.docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_clerk_cannot_listen(case_flow):
    _, _, iid = case_flow
    clerk = client_for("clerk@test.invalid")
    assert clerk.get(f"/interviews/{iid}/audio").status_code == 403


def test_batch_summons():
    c = client_for("inv@test.invalid")
    wb = Workbook()
    ws = wb.active
    ws.append(["ทะเบียนคดี กก.1 (จำลอง)"])
    ws.append(["เลขคดี", "ข้อหา", "ผู้กล่าวหา", "ที่อยู่", "พงส.", "สถานะ"])
    ws.append(["0112/2569", "ฉ้อโกง", "นายสมมติ ตัวอย่าง", "99 ม.9 ต.ตัวอย่าง จ.ทดสอบ 50000", "พ.ต.ท.ทดสอบ หนึ่ง", "อยู่ระหว่างสอบสวน"])
    ws.append(["0113/2569", "ฉ้อโกง", "นางทดลอง สมมุติ", "ต.ตัวอย่าง จ.ทดสอบ", "พ.ต.ท.ทดสอบ หนึ่ง", "อยู่ระหว่างสอบสวน"])
    ws.append(["0001/2568", "ลักทรัพย์", "นายปิดแล้ว ตัวอย่าง", "1 ต.ก จ.ข 50000", "พ.ต.ท.ทดสอบ หนึ่ง", "สิ้นสุด"])
    buf = io.BytesIO()
    wb.save(buf)
    r = c.post("/summons/import", files={"file": ("reg.xlsx", buf.getvalue())}, data={"csrf": c.csrf}, follow_redirects=False)
    imp = r.headers["location"].split("imp=")[1]
    page = c.get(f"/summons?imp={imp}").text
    assert "0112/2569" in page and "0001/2568" not in page and "ขาดบ้านเลขที่" in page
    ids = re.findall(r'name="row" value="(\d+)"[^>]*checked', page)
    assert len(ids) == 1
    r = c.post("/summons/generate", data={"csrf": c.csrf, "imp": imp, "row": ids, "date": "25/11/2569", "time": "09:00",
                                          "stagger": "on", "envelopes": "on", "place": "ห้องสอบสวน"})
    links = re.findall(r'href="(/files/[^"]+)"', r.text)
    assert len(links) == 2
    assert c.get(links[0]).content[:2] == b"PK"


def test_audit_chain_detects_tampering():
    db = SessionLocal()
    assert audit.verify_chain(db) == (True, None)
    e = db.query(AuditLog).order_by(AuditLog.id).first()
    e.detail = "แก้ย้อนหลัง"
    db.commit()
    ok, bad = audit.verify_chain(db)
    assert not ok and bad == e.id
