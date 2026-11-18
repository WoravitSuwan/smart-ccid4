"""สรุปใจความสำคัญ · เล่าเรื่อง (คำให้การแบบบรรยาย) · คำที่พบบ่อย จากเสียงสอบปากคำ.

Everything here is drawn from the words actually spoken. The language model (ai/prompts/summarize_interview.txt) may
rewrite the narrative more smoothly, but verify_text() checks the result against the transcript with plain code.
"""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz

from core.thai import MONTHS, numbers_in_draft, numbers_in_speech, to_arabic_digits

from . import qa

POLITE = {
    "ครับ",
    "ค่ะ",
    "คะ",
    "ครับผม",
    "นะครับ",
    "นะคะ",
    "จ้ะ",
    "จ้า",
    "เอ่อ",
    "อ่า",
    "อืม",
    "เออ",
    "แบบ",
    "ว่า",
    "ก็",
    "นะ",
    "น่ะ",
    "ล่ะ",
}
PRONOUNS = {"ผม", "ดิฉัน", "หนู", "ข้าฯ", "ข้าพเจ้า", "กระผม", "เขา", "เรา", "ฉัน", "ท่าน", "คุณ"}
CHANNELS = ("เฟซบุ๊ก", "เฟสบุ๊ค", "ไลน์", "LINE", "ติ๊กต็อก", "TikTok", "อินสตาแกรม", "เทเลแกรม", "Telegram", "X", "ช้อปปี้", "ลาซาด้า",
            "เว็บไซต์", "แอป", "แอปพลิเคชัน", "โทรศัพท์", "SMS", "อีเมล")  # fmt: skip
BANKS = ("กสิกรไทย", "กรุงเทพ", "กรุงไทย", "ไทยพาณิชย์", "กรุงศรี", "ทหารไทยธนชาต", "ทีทีบี", "ออมสิน", "ธ.ก.ส.", "ยูโอบี", "ซีไอเอ็มบี",
         "เกียรตินาคิน", "แลนด์ แอนด์ เฮ้าส์", "พร้อมเพย์", "ทรูมันนี่")  # fmt: skip
MONEY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:บาท|฿)")
ACCOUNT = re.compile(r"\d{3}-\d-\d{5}-\d|\d{3}-\d{1,2}-\d{4,6}-\d|\b\d{10,12}\b")
PHONE = re.compile(r"\b0[689]\d{8}\b|\b0[689]\d-\d{3}-\d{4}\b|\b02-?\d{3}-?\d{4}\b")
DATE = re.compile(r"\d{1,2}\s*(?:" + "|".join(MONTHS) + r")\s*(?:พ\.ศ\.\s*)?\d{4}")
RELATIVE = re.compile(r"(?:เมื่อวาน(?:นี้|ซืน)?|วันนี้|เมื่อเช้า|เมื่อคืน|สัปดาห์ที่แล้ว|อาทิตย์ที่แล้ว|เดือนที่แล้ว|ปีที่แล้ว)")
GENERIC = {"วันที่", "เดือน", "ปี", "เวลา", "นาฬิกา", "โมง", "บาท", "ชื่อ", "ทราบ", "เข้าใจ", "ใช่", "ไม่ใช่", "ถูกต้อง", "ประมาณ", "ตอนนั้น",
           "หลังจาก", "แล้ว", "ไม่ได้", "ได้", "ไป", "มา", "ให้", "ด้วย", "ทั้งหมด"} | set(MONTHS)  # fmt: skip
LEADING_REPLY = re.compile(r"^(?:ข้าฯ\s*)?(?:ใช่|เข้าใจ|ถูกต้อง|ไม่ทราบ|ทราบ|ไม่มี)\s+(?=\S{2,})")
SHORT_REPLY = re.compile(r"^(?:ข้าฯ\s*)?(?:ใช่|ไม่ใช่|ถูกต้อง|เข้าใจ|ไม่มี|ไม่ทราบ|ทราบ|ได้|ไม่ได้|มี|เคย|ไม่เคย)\s*$")


# ---------------------------------------------------------------- frequent words
def tokens(text: str) -> list[str]:
    try:
        from pythainlp.tokenize import word_tokenize

        return word_tokenize(text or "", engine="newmm", keep_whitespace=False)
    except ImportError:  # the tokenizer is optional: fall back to phrases
        return (text or "").split()


def stopwords() -> set[str]:
    try:
        from pythainlp.corpus import thai_stopwords

        return set(thai_stopwords())
    except ImportError:
        return set()


def keywords(texts: list[str], top: int = 20) -> list[list]:
    """คำที่พบบ่อย: words of 2+ letters, no particles, pronouns, stopwords or bare numbers."""
    stop = stopwords() | POLITE | PRONOUNS | GENERIC
    counts = Counter()
    for text in texts:
        for w in tokens(to_arabic_digits(text)):
            w = w.strip(" ,.?!\"'()“”-–")
            if len(w) < 2 or w in stop or re.fullmatch(r"[\d,.\-/:]+", w):
                continue
            counts[w] += 1
    return [[w, n] for w, n in counts.most_common(top) if n >= 2] or [[w, n] for w, n in counts.most_common(min(top, 10))]


# ---------------------------------------------------------------- key facts and code-only drafts
def unique(items):
    return list(dict.fromkeys(i.strip() for i in items if i and i.strip()))


def key_facts(text: str, subject_text: str | None = None) -> dict[str, list[str]]:
    t = to_arabic_digits(text or "")
    said = to_arabic_digits(subject_text if subject_text is not None else text or "")
    phones = unique(PHONE.findall(t))
    digits_of = {re.sub(r"\D", "", p) for p in phones}
    return {
        "วันเวลาที่กล่าวถึง": unique(DATE.findall(t) + RELATIVE.findall(said)),
        "จำนวนเงิน": unique(f"{m} บาท" for m in MONEY.findall(t)),
        "เลขบัญชี": unique(a for a in ACCOUNT.findall(t) if re.sub(r"\D", "", a) not in digits_of),
        "ธนาคาร/ช่องทางการเงิน": unique(b for b in BANKS if b in t),
        "เบอร์โทรศัพท์": phones,
        "ช่องทางที่เกี่ยวข้อง": unique(c for c in CHANNELS if re.search(rf"(?<![A-Za-z]){re.escape(c)}(?![A-Za-z])", t)),
    }


def subject_answers(pairs: list[qa.Pair], segs: list[qa.Seg]) -> list[str]:
    """The subject's own words, in order (official first person), without one-word replies."""
    if pairs:
        answers = [p.answer for p in pairs]
    else:
        answers = [qa.to_official_answer(s.text) for s in segs if s.role == "subject"]
    return [a for a in answers if not SHORT_REPLY.match(a.strip())]


def code_points(pairs, segs) -> list[str]:
    answers = subject_answers(pairs, segs)
    subject_text = " ".join(s.text for s in segs if s.role == "subject")
    points = []
    story = next((a for a in answers if len(a) > 40), answers[0] if answers else "")
    if story:
        points.append(f"เหตุการณ์โดยย่อ: {story.removeprefix('ข้าฯ ').strip()}")
    for label, values in key_facts(" ".join(s.text for s in segs), subject_text).items():
        if values:
            points.append(f"{label}: {', '.join(values[:6])}")
    if not any(p.startswith("จำนวนเงิน") for p in points) and re.search(r"เงิน|โอน", subject_text):
        points.append("จำนวนเงิน: ผู้ให้การกล่าวถึงการโอนเงิน แต่ไม่ได้ระบุจำนวนที่ชัดเจน")
    wish = next((a for a in answers if re.search(r"ประสงค์|ดำเนินคดี|ติดตามเงิน", a)), "")
    if wish:
        points.append(f"ความประสงค์: {wish.removeprefix('ข้าฯ ').strip()}")
    return points


def spoken_sentences(segs) -> list[str]:
    """The subject's own sentences in order: pronouns → ข้าฯ, fillers and particles removed, nothing added."""
    out = []
    for s in segs:
        if s.role != "subject":
            continue
        t = qa.to_official_answer(s.text)
        if SHORT_REPLY.match(t.strip()):
            continue
        t = LEADING_REPLY.sub("ข้าฯ ", t)  # "ใช่ ข้าฯ เปิดบัญชี" → "ข้าฯ เปิดบัญชี": a bare yes/no has no meaning in a story
        # to_official_answer always starts with ข้าฯ; keep it only where the speaker used a pronoun
        if not qa.clean(s.text).startswith(("ผม", "ดิฉัน", "กระผม", "หนู", "ฉัน", "ข้าพเจ้า", "เรา")):
            t = t.removeprefix("ข้าฯ").strip()
        out.append(t)
    return out


def code_narrative(pairs, segs) -> str:
    """คำให้การแบบบรรยาย: ข้าฯ ขอให้การว่า … then the subject's sentences, three to a paragraph."""
    sentences = spoken_sentences(segs) or [a for a in subject_answers(pairs, segs)]
    if not sentences:
        return ""
    sentences[0] = "ข้าฯ ขอให้การว่า " + sentences[0].removeprefix("ข้าฯ").strip()
    return "\n".join(" ".join(sentences[i : i + 3]) for i in range(0, len(sentences), 3))


# ---------------------------------------------------------------- source check for free text
def verify_text(text: str, segs: list[qa.Seg]) -> list[str]:
    """Figures, long digit runs and phrases of a narrative must be found somewhere in the recording."""
    source = " ".join(s.text for s in segs)
    heard = numbers_in_speech(source)
    flags = []
    for literal, value in numbers_in_draft(re.sub(r"x+[\dx\-]*", " ", text or "")):
        if value not in heard:
            flags.append(f"ตัวเลข {literal} ไม่พบในเสียง")
    digits = re.sub(r"\D", "", to_arabic_digits(source))
    for run in qa.digit_runs(text):
        if run not in digits:
            flags.append(f"เลข {run} ไม่พบในเสียง")
    norm_src = qa.normalize(source)
    for phrase in qa.split_sentences(text):
        body = phrase.removeprefix("ข้าฯ").strip()
        if len(qa.normalize(body)) < 8 or body in ("ขอให้การว่า",):
            continue
        if fuzz.partial_ratio(qa.normalize(body), norm_src) < 70:
            flags.append(f"หาต้นทางในเสียงไม่ได้: “{body[:60]}”")
    return list(dict.fromkeys(flags))


# ---------------------------------------------------------------- speaker guess when there is no diarization model
QUESTION = re.compile(
    r"(?:ไหม|มั้ย|หรือไม่|หรือเปล่า|รึเปล่า|อะไร|เมื่อไร|เมื่อไหร่|ที่ไหน|อย่างไร|ยังไง|เท่าไร|เท่าไหร่|ใคร|ทำไม|กี่|หรือยัง|ใช่ไหม|ใช่หรือไม่)\s*(?:ครับ|คะ|ค่ะ)?\s*\??\s*$"
)
OFFICER_PHRASES = re.compile(
    r"พนักงานสอบสวน|ขอแจ้ง|มีสิทธิ|ข้อกล่าวหา|บันทึกเสียง|ขอให้ท่าน|ท่านจะ|ท่านทราบ|ท่านเข้าใจ|เล่าให้ฟัง|ช่วยเล่า"
)


def guess_speaker(text: str) -> str:
    """S1 = investigator (questions, rights, procedure) · S2 = the person giving the statement."""
    t = (text or "").strip()
    return "S1" if QUESTION.search(t) or OFFICER_PHRASES.search(t) else "S2"
