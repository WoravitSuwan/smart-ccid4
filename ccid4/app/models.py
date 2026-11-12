from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = {
    "investigator": "พนักงานสอบสวน",
    "clerk": "ผู้ช่วย / ธุรการ",
    "chief": "หัวหน้า",
    "admin": "ผู้ดูแลระบบ",
}
STAGES = ["รับเรื่อง", "สอบสวน", "สรุปสำนวน", "ส่งอัยการ"]
PERSON_ROLES = ["ผู้กล่าวหา", "ผู้ต้องหา", "พยาน"]
SPEAKER_ROLES = {"investigator": "พนักงานสอบสวน", "subject": "ผู้ให้ถ้อยคำ", "lawyer": "ทนายความ/ผู้ไว้วางใจ", "other": "อื่น ๆ"}


def now() -> datetime:
    return datetime.now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[str] = mapped_column(String(20), default="investigator")
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    @property
    def role_name(self) -> str:
        return ROLES.get(self.role, self.role)

    def can(self, perm: str) -> bool:
        table = {
            "view_all_cases": {"chief"},
            "edit_case": {"investigator", "chief", "clerk"},
            "listen_audio": {"investigator", "chief"},
            "confirm_statement": {"investigator"},
            "summons": {"investigator", "clerk", "chief"},
            "view_audit": {"chief", "admin"},
            "manage_users": {"admin"},
        }
        return self.role in table.get(perm, set())


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_no: Mapped[str] = mapped_column(String(50), index=True)
    receipt_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    charge: Mapped[str] = mapped_column(String(400), default="")
    case_type: Mapped[str] = mapped_column(String(20), default="online")  # online | general
    stage: Mapped[str] = mapped_column(String(30), default="รับเรื่อง")
    status: Mapped[str] = mapped_column(String(60), default="อยู่ระหว่างสอบสวน")
    summary: Mapped[str] = mapped_column(Text, default="")
    damage_amount: Mapped[float] = mapped_column(Float, default=0)
    investigator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    investigator: Mapped[User | None] = relationship()
    persons: Mapped[list[Person]] = relationship(back_populates="case", cascade="all, delete-orphan", order_by="Person.id")
    deadlines: Mapped[list[Deadline]] = relationship(back_populates="case", cascade="all, delete-orphan", order_by="Deadline.due_at")
    interviews: Mapped[list[Interview]] = relationship(back_populates="case", cascade="all, delete-orphan", order_by="Interview.id.desc()")

    @property
    def type_name(self) -> str:
        return "ออนไลน์" if self.case_type == "online" else "อาญาทั่วไป"

    @property
    def complainant(self) -> Person | None:
        return next((p for p in self.persons if p.role == "ผู้กล่าวหา"), None)


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    citizen_id: Mapped[str] = mapped_column(String(30), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    address: Mapped[str] = mapped_column(String(400), default="")
    postcode: Mapped[str] = mapped_column(String(10), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    case: Mapped[Case] = relationship(back_populates="persons")


class Deadline(Base):
    __tablename__ = "deadlines"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # ฝากขัง ผัดฟ้อง ส่งสำนวน นัดหมาย อื่น ๆ
    title: Mapped[str] = mapped_column(String(300))
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    basis: Mapped[str] = mapped_column(String(400), default="")
    legal_ref: Mapped[str] = mapped_column(String(300), default="[อ้างอิงกฎหมาย]")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    case: Mapped[Case] = relationship(back_populates="deadlines")


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="checklist")
    # checklist -> recording -> recorded -> transcribed -> drafted -> confirmed
    checklist_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    audio_path: Mapped[str] = mapped_column(String(400), default="")
    audio_mime: Mapped[str] = mapped_column(String(80), default="")
    audio_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_until_sec: Mapped[float] = mapped_column(Float, default=0)
    duration_sec: Mapped[float] = mapped_column(Float, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    marks_json: Mapped[str] = mapped_column(Text, default="[]")
    rights_manual_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    case: Mapped[Case] = relationship(back_populates="interviews")
    person: Mapped[Person | None] = relationship()
    confirmed_by: Mapped[User | None] = relationship(foreign_keys=[confirmed_by_id])
    segments: Mapped[list[Segment]] = relationship(back_populates="interview", cascade="all, delete-orphan", order_by="Segment.start")
    pairs: Mapped[list[QAPair]] = relationship(back_populates="interview", cascade="all, delete-orphan", order_by="QAPair.idx")


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    speaker: Mapped[str] = mapped_column(String(20), default="S1")
    role: Mapped[str] = mapped_column(String(20), default="other")
    text: Mapped[str] = mapped_column(Text)
    interview: Mapped[Interview] = relationship(back_populates="segments")


class QAPair(Base):
    __tablename__ = "qa_pairs"
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[float] = mapped_column(Float, default=0)
    end: Mapped[float] = mapped_column(Float, default=0)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    interview: Mapped[Interview] = relationship(back_populates="pairs")


class RegisterRow(Base):
    """One row of the unit's case register (imported from Excel) for batch summons."""
    __tablename__ = "register_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[str] = mapped_column(String(40), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    case_no: Mapped[str] = mapped_column(String(60), default="")
    receipt_no: Mapped[str] = mapped_column(String(60), default="")
    charge: Mapped[str] = mapped_column(String(400), default="")
    complainant: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(400), default="")
    postcode: Mapped[str] = mapped_column(String(10), default="")
    investigator: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(60), default="")
    problem: Mapped[str] = mapped_column(String(200), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_name: Mapped[str] = mapped_column(String(200), default="")
    action: Mapped[str] = mapped_column(String(40), index=True)
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str] = mapped_column(String(40), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(60), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")
