"""Interview-room logic: build a Q&A draft from speaker-labelled segments,
flag figures not heard in the audio, and check that rights were read out.

The draft keeps the investigator's own questions and the speaker's own
answers; it only pairs them up and applies the official first-person
"ข้าฯ". Nothing is paraphrased, which keeps the meaning of the statement
intact. Every pair keeps the start/end time of the audio it came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .thai import mmss, numbers_in_draft, numbers_in_speech


@dataclass
class Seg:
    start: float
    end: float
    role: str  # investigator | subject | lawyer | other
    text: str


@dataclass
class Pair:
    question: str
    answer: str
    start: float
    end: float


_PRONOUN = re.compile(r"^(?:กระผม|ผม|ดิฉัน|ดิชั้น|หนู|ฉัน|เรา|ข้าพเจ้า)\s*")
_PARTICLES = re.compile(r"\s*(?:ครับผม|ครับ|ค่ะ|คะ|นะครับ|นะคะ|จ้ะ|จ้า)(?=[\s,.]|$)")
_YES = re.compile(r"^(?:ใช่|ถูกต้อง|ได้|มี|เคย)(?=\s|$)")


def clean_question(text: str) -> str:
    t = _PARTICLES.sub("", text.strip()).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def to_official_answer(text: str) -> str:
    """Rewrite a spoken answer in the official first person, without paraphrasing."""
    t = _PARTICLES.sub("", text.strip()).strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(?<=\s)(?:กระผม|ผม|ดิฉัน|หนู)(?=\s)", "ข้าฯ", f" {t} ").strip()
    if _PRONOUN.match(t):
        t = _PRONOUN.sub("ข้าฯ ", t, count=1)
    elif not t.startswith("ข้าฯ"):
        t = "ข้าฯ " + t
    return t.strip()


def build_pairs(segments: list[Seg], max_gap: float = 20.0) -> list[Pair]:
    """Investigator turns become questions; the following subject turns become the answer.

    Lawyer/other turns are not put into the Q&A (they stay in the transcript).
    Answers given without a preceding question are attached to the previous pair.
    """
    pairs: list[Pair] = []
    q_parts: list[Seg] = []
    a_parts: list[Seg] = []

    def flush():
        if q_parts and a_parts:
            pairs.append(Pair(
                question=clean_question(" ".join(s.text for s in q_parts)),
                answer=to_official_answer(" ".join(s.text for s in a_parts)),
                start=q_parts[0].start, end=a_parts[-1].end,
            ))
        elif a_parts and pairs:
            p = pairs[-1]
            p.answer = p.answer + " " + to_official_answer(" ".join(s.text for s in a_parts)).removeprefix("ข้าฯ ").strip()
            p.end = a_parts[-1].end

    for s in sorted(segments, key=lambda x: x.start):
        if s.role == "investigator":
            if a_parts:
                flush()
                q_parts, a_parts = [], []
            elif q_parts and s.start - q_parts[-1].end > max_gap:
                # Earlier investigator speech got no answer (e.g. reading rights): keep it in the
                # transcript only, and start a fresh question.
                q_parts = []
            q_parts.append(s)
        elif s.role == "subject":
            a_parts.append(s)
    flush()
    return pairs


@dataclass
class NumberFlag:
    literal: str
    value: int
    heard_nearby: list[int] = field(default_factory=list)

    @property
    def message(self) -> str:
        if self.heard_nearby:
            said = ", ".join(f"{n:,}" for n in self.heard_nearby[:3])
            return f"ตัวเลข {self.literal} ไม่พบในเสียงช่วงนี้ (ได้ยิน {said})"
        return f"ตัวเลข {self.literal} ไม่พบในเสียงช่วงนี้"


def number_flags(answer: str, question: str, segments: list[Seg], start: float, end: float,
                 slack: float = 5.0) -> list[NumberFlag]:
    """Figures in the draft that were not said in the audio of this pair (± slack seconds).

    Masked account numbers (xxx-x-x1234-x) are ignored because they are not spoken as written.
    """
    spoken = " ".join(s.text for s in segments if s.end >= start - slack and s.start <= end + slack)
    heard = numbers_in_speech(spoken)
    flags = []
    text = re.sub(r"x+[\dx\-]*", " ", answer + " " + question)
    seen = set()
    for literal, value in numbers_in_draft(text):
        if value in heard or value in seen:
            continue
        seen.add(value)
        flags.append(NumberFlag(literal=literal, value=value, heard_nearby=sorted(heard - {value})))
    return flags


# Rights under Criminal Procedure Code s.134/4 that must be read out before questioning a suspect.
RIGHTS = [
    ("silence", "สิทธิให้การหรือไม่ให้การก็ได้", [r"ให้การหรือไม่(?:ให้การ)?ก็ได้", r"สิทธิ(?:ที่จะ)?ไม่ให้การ", r"จะให้การหรือไม่"]),
    ("evidence", "ถ้อยคำอาจใช้เป็นพยานหลักฐาน", [r"(?:อาจ)?ใช้เป็นพยานหลักฐาน"]),
    ("counsel", "สิทธิมีทนายความหรือผู้ไว้วางใจเข้าฟัง", [r"ทนายความหรือผู้(?:ซึ่ง)?(?:ตน|ท่าน)?ไว้วางใจ", r"สิทธิ(?:ให้|มี)ทนาย", r"ผู้ไว้วางใจเข้าฟัง"]),
]


@dataclass
class RightCheck:
    key: str
    label: str
    found_at: float | None

    @property
    def ok(self) -> bool:
        return self.found_at is not None

    @property
    def when(self) -> str:
        return mmss(self.found_at) if self.found_at is not None else ""


def check_rights(segments: list[Seg]) -> list[RightCheck]:
    """Search the investigator's speech for each right; report where it was first said."""
    out = []
    inv = sorted([s for s in segments if s.role == "investigator"], key=lambda s: s.start)
    for key, label, patterns in RIGHTS:
        found = None
        for s in inv:
            compact = re.sub(r"\s+", "", s.text)
            if any(re.search(p, compact) for p in patterns):
                found = s.start
                break
        out.append(RightCheck(key, label, found))
    return out


def confirm_blockers(pairs_reviewed: list[bool], rights: list[RightCheck], manual_rights_ok: bool,
                     is_suspect: bool) -> list[str]:
    reasons = []
    left = sum(1 for r in pairs_reviewed if not r)
    if not pairs_reviewed:
        reasons.append("ยังไม่มีร่างคำให้การ")
    elif left:
        reasons.append(f"ยังไม่ได้ตรวจ {left} คู่")
    if is_suspect and not all(r.ok for r in rights) and not manual_rights_ok:
        missing = sum(1 for r in rights if not r.ok)
        reasons.append(f"ตรวจไม่พบการแจ้งสิทธิ {missing} ข้อในเสียง ต้องฟังและยืนยันด้วยตนเอง")
    return reasons
