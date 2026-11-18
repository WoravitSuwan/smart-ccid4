"""Q&A drafting, source checks and rights checks for recorded interviews.

Everything here is plain code (no model), so the checks cannot be talked out of a flag:
- build_pairs: investigator turns become questions, the subject's turns become answers in the official "ข้าฯ" form,
  without paraphrasing; every pair keeps its audio times and source segment ids.
- verify: figures, long digit runs (accounts, phones) and every sentence of an answer must be found in the source audio
  text of that pair; anything else is flagged.
- check_rights: the recording notice, the charge, and the three rights of ป.วิ.อาญา ม.134/4 in the investigator's speech.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from core.thai import mmss, numbers_in_draft, numbers_in_speech, to_arabic_digits


@dataclass
class Seg:
    id: int
    start: float
    end: float
    role: str  # investigator | subject | lawyer | trusted | interpreter | recorder | ""
    text: str


@dataclass
class Pair:
    question: str
    answer: str
    start: float
    end: float
    segment_ids: list[int]
    flags: list[str] = field(default_factory=list)


_PRONOUN = re.compile(r"^(?:กระผม|ผม|ดิฉัน|ดิชั้น|หนู|ฉัน|เรา|ข้าพเจ้า)\s*")
_PARTICLES = re.compile(r"\s*(?:ครับผม|ครับ|ค่ะ|คะ|นะครับ|นะคะ|จ้ะ|จ้า)(?=[\s,.]|$)")
_FILLERS = re.compile(r"(?:^|\s)(?:เอ่อ+|อ่า+|อืม+|เออ+|แบบว่า)(?=\s|$)")


def clean(text: str) -> str:
    t = _PARTICLES.sub("", (text or "").strip())
    t = _FILLERS.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_question(text: str) -> str:
    return clean(text)


def to_official_answer(text: str) -> str:
    """Spoken answer → official first person ("ข้าฯ"), removing fillers only; the words stay the speaker's own."""
    t = clean(text)
    # a pronoun that starts a word (after a space) → ข้าฯ; Thai has no space before the next word ("ผมเปิด")
    t = re.sub(r"(?<=\s)(?:กระผม|ผม|ดิฉัน)(?=\s|[ก-๙])", "ข้าฯ ", f" {t} ")
    t = re.sub(r"(?<=[ก-๙])(?:ดิฉัน|กระผม)", " ข้าฯ ", t)  # object pronoun after a word ("บล็อกไลน์ดิฉัน")
    # "ผม" after a word only when a verb/particle follows ("ส่งไปผมก็", "เฟซบุ๊กผมไป") — not "ผมยาว" (hair)
    t = re.sub(r"(?<=[ก-๙])ผม(?=ก็|ไม่|ได้|จะ|เคย|เป็น|มี|ไป|แล้ว|เอง|ด้วย|\s|$)", " ข้าฯ ", t)
    t = re.sub(r"ข้าฯ\s+", "ข้าฯ ", t).strip()
    if _PRONOUN.match(t):
        t = _PRONOUN.sub("ข้าฯ ", t, count=1)
    elif not t.startswith("ข้าฯ"):
        t = "ข้าฯ " + t
    return t.strip()


def build_pairs(segments: list[Seg], max_gap: float = 20.0) -> list[Pair]:
    """Lawyer/other turns stay in the transcript only. Answers without a question join the previous pair."""
    pairs: list[Pair] = []
    q_parts: list[Seg] = []
    a_parts: list[Seg] = []

    def flush():
        if q_parts and a_parts:
            pairs.append(
                Pair(
                    question=clean_question(" ".join(s.text for s in q_parts)),
                    answer=to_official_answer(" ".join(s.text for s in a_parts)),
                    start=q_parts[0].start,
                    end=a_parts[-1].end,
                    segment_ids=[s.id for s in q_parts + a_parts],
                )
            )
        elif a_parts and pairs:
            p = pairs[-1]
            extra = to_official_answer(" ".join(s.text for s in a_parts)).removeprefix("ข้าฯ ").strip()
            p.answer = f"{p.answer} {extra}"
            p.end = a_parts[-1].end
            p.segment_ids += [s.id for s in a_parts]

    for s in sorted(segments, key=lambda x: x.start):
        if s.role == "investigator":
            if a_parts:
                flush()
                q_parts, a_parts = [], []
            elif q_parts and s.start - q_parts[-1].end > max_gap:
                q_parts = []  # e.g. rights read out with no answer: transcript only
            q_parts.append(s)
        elif s.role == "subject":
            a_parts.append(s)
    flush()
    return pairs


# ---------------------------------------------------------------- source checks
LONG_DIGITS = re.compile(r"\d[\d\-\s]{4,}\d")


def digit_runs(text: str) -> set[str]:
    return {
        re.sub(r"\D", "", m.group())
        for m in LONG_DIGITS.finditer(to_arabic_digits(text or ""))
        if len(re.sub(r"\D", "", m.group())) >= 6
    }


def verify(pair: Pair, segments: dict[int, Seg], slack: float = 5.0, all_segments: list[Seg] | None = None) -> list[str]:
    """Flags for one pair. Source = the pair's own segments (plus nearby audio for figures)."""
    flags = []
    src_segs = [segments[i] for i in pair.segment_ids if i in segments]
    if not src_segs:
        return ["หาช่วงเสียงต้นทางไม่ได้"]
    src = " ".join(s.text for s in src_segs)
    nearby = src
    if all_segments:
        nearby = " ".join(s.text for s in all_segments if s.end >= pair.start - slack and s.start <= pair.end + slack)
    heard = numbers_in_speech(nearby)
    text = re.sub(r"x+[\dx\-]*", " ", f"{pair.question} {pair.answer}")
    for literal, value in numbers_in_draft(text):
        if value not in heard:
            flags.append(f"ตัวเลข {literal} ไม่พบในเสียงช่วงนี้")
    src_digits = re.sub(r"\D", "", to_arabic_digits(nearby))
    for run in digit_runs(text):
        if run not in src_digits:
            flags.append(f"เลข {run} (บัญชี/โทรศัพท์) ไม่พบในเสียงช่วงนี้")
    answer_src = clean(" ".join(s.text for s in src_segs if s.role == "subject")) or clean(src)
    for sentence in split_sentences(pair.answer):
        body = sentence.removeprefix("ข้าฯ").strip()
        if len(body) < 8:
            continue
        if fuzz.partial_ratio(normalize(body), normalize(answer_src)) < 70:
            flags.append(f"หาต้นทางในเสียงไม่ได้: “{body[:60]}”")
    return sorted(set(flags), key=flags.index)


def split_sentences(text: str) -> list[str]:
    """Thai has no sentence punctuation: a space marks a phrase/sentence break, so every phrase is checked."""
    parts = re.split(r"\s+", text or "")
    return [p.strip() for p in parts if p.strip()]


def normalize(text: str) -> str:
    t = to_arabic_digits(text or "")
    t = re.sub(r"(?:กระผม|ผม|ดิฉัน|หนู|ข้าพเจ้า|ข้าฯ)", "", t)
    return re.sub(r"[\s,.\-–]+", "", t)


def uncovered(segments: list[Seg], pairs: list[Pair]) -> list[int]:
    """Segments where the subject spoke but that are not used by any answer."""
    used = {i for p in pairs for i in p.segment_ids}
    return [s.id for s in segments if s.role == "subject" and s.id not in used and len(clean(s.text)) > 3]


# ---------------------------------------------------------------- rights
RIGHTS = [
    ("recording", "แจ้งว่ามีการบันทึกเสียง", [r"บันทึกเสียง", r"อัดเสียง"]),
    ("charge", "แจ้งข้อกล่าวหา (ป.วิ.อาญา ม.134)", [r"แจ้งข้อกล่าวหา", r"ข้อกล่าวหาว่า", r"ถูกกล่าวหาว่า"]),
    (
        "silence",
        "สิทธิให้การหรือไม่ให้การก็ได้ (ม.134/4)",
        [r"ให้การหรือไม่(?:ให้การ)?ก็ได้", r"สิทธิ(?:ที่จะ)?ไม่ให้การ", r"จะให้การหรือไม่"],
    ),
    ("evidence", "ถ้อยคำอาจใช้เป็นพยานหลักฐาน (ม.134/4)", [r"(?:อาจ)?ใช้เป็นพยานหลักฐาน"]),
    (
        "counsel",
        "สิทธิให้ทนายความหรือผู้ไว้วางใจเข้าฟัง (ม.134/4)",
        [r"ทนายความหรือผู้(?:ซึ่ง)?(?:ตน|ท่าน)?ไว้วางใจ", r"สิทธิ(?:ให้|มี)ทนาย", r"ผู้ไว้วางใจเข้าฟัง"],
    ),
]


@dataclass
class RightFound:
    key: str
    label: str
    found_at: float | None

    @property
    def ok(self) -> bool:
        return self.found_at is not None

    @property
    def when(self) -> str:
        return mmss(self.found_at) if self.found_at is not None else ""


def check_rights(segments: list[Seg]) -> list[RightFound]:
    inv = sorted([s for s in segments if s.role == "investigator"], key=lambda s: s.start)
    out = []
    for key, label, patterns in RIGHTS:
        found = next((s.start for s in inv if any(re.search(p, re.sub(r"\s+", "", s.text)) for p in patterns)), None)
        out.append(RightFound(key, label, found))
    return out


def confirm_blockers(reviewed: list[bool], rights_ok: bool, override_reason: str, is_suspect: bool) -> list[str]:
    reasons = []
    left = sum(1 for r in reviewed if not r)
    if not reviewed:
        reasons.append("ยังไม่มีร่างคำให้การ")
    elif left:
        reasons.append(f"ยังไม่ได้ตรวจ {left} คู่ถาม–ตอบ")
    if is_suspect and not rights_ok and len((override_reason or "").strip()) < 10:
        reasons.append("ตรวจไม่พบการแจ้งสิทธิครบในเสียง ต้องฟังเองและระบุเหตุผลก่อนยืนยัน")
    return reasons
