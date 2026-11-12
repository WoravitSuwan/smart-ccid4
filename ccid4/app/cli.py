"""Command line tools.

    python -m app.cli create-admin --email admin@police.go.th --name "ผู้ดูแลระบบ"
    python -m app.cli demo        # fictional sample data for training (never on a live database)
    python -m app.cli verify-audit
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timedelta

from sqlalchemy import select

from . import audit
from .db import SessionLocal, init_db
from .models import Case, Deadline, Person, User
from .security import hash_password, password_problem


def create_user(email: str, name: str, role: str, password: str | None, position: str = "", phone: str = "") -> None:
    db = SessionLocal()
    if db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none():
        sys.exit(f"มีผู้ใช้ {email} แล้ว")
    password = password or getpass.getpass("รหัสผ่าน: ")
    if problem := password_problem(password):
        sys.exit(problem)
    u = User(email=email.lower(), full_name=name, role=role, position=position, phone=phone, password_hash=hash_password(password))
    db.add(u)
    db.flush()
    audit.log(db, None, "create", "user", u.id, f"สร้างผู้ใช้ {u.email} จากคำสั่งระบบ")
    print(f"สร้าง {u.email} ({role}) แล้ว")


DEMO_ACCOUNTS = [
    ("inv.demo@example.invalid", "demo-inv-2569", "พนักงานสอบสวน"),
    ("chief.demo@example.invalid", "demo-chief-2569", "หัวหน้า"),
    ("clerk.demo@example.invalid", "demo-clerk-2569", "ธุรการ"),
]


def seed_demo(db) -> bool:
    """Fictional data only: names, case numbers and accounts are made up. Returns False if data exists."""
    if db.execute(select(User)).first():
        return False
    users = {
        "chief": User(email="chief.demo@example.invalid", full_name="พ.ต.อ.ทดสอบ หัวหน้า", position="ผู้กำกับการ", role="chief",
                      password_hash=hash_password("demo-chief-2569")),
        "inv": User(email="inv.demo@example.invalid", full_name="พ.ต.ท.ทดสอบ หนึ่ง", position="สารวัตร (สอบสวน)", phone="00 0000 0001",
                    role="investigator", password_hash=hash_password("demo-inv-2569")),
        "clerk": User(email="clerk.demo@example.invalid", full_name="ส.ต.อ.ทดสอบ ธุรการ", position="ธุรการ", role="clerk",
                      password_hash=hash_password("demo-clerk-2569")),
    }
    db.add_all(users.values())
    db.flush()
    c = Case(case_no="0112/2569", receipt_no="2569-10-00418", charge="ฉ้อโกงประชาชนผ่านระบบคอมพิวเตอร์", case_type="online",
             stage="สอบสวน", status="อยู่ระหว่างสอบสวน", investigator_id=users["inv"].id, damage_amount=486500,
             summary="ถูกชักชวนลงทุนผ่านกลุ่มสนทนาออนไลน์ โอนเงิน 3 ครั้ง (ข้อมูลจำลอง)")
    c2 = Case(case_no="0098/2569", charge="ฉ้อโกงผ่านระบบคอมพิวเตอร์ (ซื้อขายออนไลน์)", case_type="online", stage="สรุปสำนวน",
              status="อยู่ระหว่างสอบสวน", investigator_id=users["inv"].id)
    db.add_all([c, c2])
    db.flush()
    base = datetime.now().replace(hour=16, minute=30, second=0, microsecond=0)
    db.add_all([
        Person(case_id=c.id, role="ผู้กล่าวหา", name="นายสมมติ ตัวอย่าง", citizen_id="3000000000456", address="99 ม.9 ต.ตัวอย่าง อ.เมืองสมมติ จ.ทดสอบ", postcode="50000"),
        Person(case_id=c.id, role="ผู้ต้องหา", name="นายทดลอง ไม่มีจริง", citizen_id="1000000000123"),
        Person(case_id=c.id, role="พยาน", name="นางสาวตัวอย่าง ใจงาม"),
        Person(case_id=c2.id, role="ผู้กล่าวหา", name="นางสาวทดสอบ ใจดี"),
        Deadline(case_id=c.id, kind="ฝากขัง", title="ฝากขังครั้งที่ 3 ครบกำหนด", due_at=base + timedelta(days=2),
                 basis="ศาลอนุญาตฝากขังครั้งที่ 2 (ข้อมูลจำลอง)"),
        Deadline(case_id=c2.id, kind="ส่งสำนวน", title="ส่งสำนวนต่ออัยการ", due_at=base + timedelta(days=1)),
    ])
    audit.log(db, None, "create", "case", c.id, "ใส่ข้อมูลจำลองสำหรับฝึกใช้งาน")
    return True


def demo() -> None:
    if not seed_demo(SessionLocal()):
        sys.exit("ฐานข้อมูลมีผู้ใช้อยู่แล้ว ไม่ใส่ข้อมูลจำลอง")
    print("ใส่ข้อมูลจำลองแล้ว: " + " · ".join(f"{e} / {p}" for e, p, _ in DEMO_ACCOUNTS))


def main() -> None:
    init_db()
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--password")
    u = sub.add_parser("create-user")
    u.add_argument("--email", required=True)
    u.add_argument("--name", required=True)
    u.add_argument("--role", choices=["investigator", "clerk", "chief", "admin"], required=True)
    u.add_argument("--position", default="")
    u.add_argument("--phone", default="")
    u.add_argument("--password")
    sub.add_parser("demo")
    sub.add_parser("verify-audit")
    args = ap.parse_args()
    if args.cmd == "create-admin":
        create_user(args.email, args.name, "admin", args.password)
    elif args.cmd == "create-user":
        create_user(args.email, args.name, args.role, args.password, args.position, args.phone)
    elif args.cmd == "demo":
        demo()
    else:
        ok, bad = audit.verify_chain(SessionLocal())
        print("ประวัติการใช้งานต่อเนื่อง ไม่ถูกแก้ไข" if ok else f"พบการแก้ไขที่รายการ #{bad}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
