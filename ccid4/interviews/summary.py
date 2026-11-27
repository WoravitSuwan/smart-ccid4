"""สรุปใจความสำคัญ · เล่าเรื่อง (คำให้การแบบบรรยาย) · คำที่พบบ่อย จากเสียงสอบปากคำ.

Everything here is drawn from the words actually spoken. The language model (ai/prompts/summarize_interview.txt) may
rewrite the narrative more smoothly, but verify_text() checks the result against the transcript with plain code.
"""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz

from core.thai import MONTHS, mmss, numbers_in_draft, numbers_in_speech, thai_words_to_int, to_arabic_digits

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


NUMBER_WORDS = re.compile(r"(?:ศูนย์|หนึ่ง|เอ็ด|สอง|ยี่|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|ร้อย|พัน|หมื่น|แสน|ล้าน)+")
MONEY_CONTEXT = re.compile(r"เงิน|โอน|ยอด|ออเดอร์|ถอน|เติม|จ่าย|ค่า|ราคา|กำไร|คืน|ซื้อ|บาท")


def spoken_amounts(text: str) -> list[str]:
    """Amounts said as words ("สองแสนกว่าบาท", "หนึ่งแสนแปด") next to money talk, with what was said for checking."""
    t = to_arabic_digits(text or "")
    out = []
    for m in NUMBER_WORDS.finditer(t):
        value = thai_words_to_int(m.group(), colloquial=True)
        if value is None or value < 100:
            continue
        after, before = t[m.end() : m.end() + 12], t[max(0, m.start() - 30) : m.start()]
        if not (re.match(r"\s*(?:กว่า)?\s*บาท", after) or MONEY_CONTEXT.search(before)):
            continue
        more = "กว่า" if re.match(r"\s*กว่า", after) else ""
        out.append(f"{value:,}{' ' + more if more else ''} บาท (พูดว่า “{m.group()}{more}”)")
    return unique(out)


def key_facts(text: str, subject_text: str | None = None) -> dict[str, list[str]]:
    t = to_arabic_digits(text or "")
    said = to_arabic_digits(subject_text if subject_text is not None else text or "")
    phones = unique(PHONE.findall(t))
    digits_of = {re.sub(r"\D", "", p) for p in phones}
    return {
        "วันเวลาที่กล่าวถึง": unique(DATE.findall(t) + RELATIVE.findall(said)),
        "จำนวนเงิน": unique([f"{m} บาท" for m in MONEY.findall(t)] + spoken_amounts(said)),
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


KEY_VERBS = re.compile(r"โอน|ถอน|เติม|จ่าย|ฝาก|สั่งซื้อ|ออเดอร์|สมัคร|ลงทุน|หลอก|ติดต่อไม่ได้|บล็อก|ปิด(?:บัญชี|ร้าน)|ทัก|ชวน")


def key_sentences(segs, limit: int = 8) -> list[str]:
    """The sentences that carry the story (money, transfers, apps, dates), in the order they were said, with their time.

    An extract, not a rewrite: every word is the speaker's own, so it can be checked against the audio at that time.
    """
    scored = []
    for s in segs:
        if s.role != "subject":
            continue
        text = tidy(spoken_sentence(s.text))
        if len(text) < 12:
            continue
        t = to_arabic_digits(text)
        score = 3 * bool(MONEY.search(t) or spoken_amounts(t)) + 2 * bool(KEY_VERBS.search(t))
        score += 2 * any(c in t for c in CHANNELS + BANKS) + 2 * bool(DATE.search(t) or RELATIVE.search(t))
        if score >= 3:
            scored.append((score, s.start, text))
    best = sorted(sorted(scored, key=lambda x: -x[0])[:limit], key=lambda x: x[1])
    return [f"นาที {mmss(start)}: {shorten(text)}" for _, start, text in best]


def shorten(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip() + " …"


def code_points(pairs, segs) -> list[str]:
    answers = subject_answers(pairs, segs)
    subject_text = " ".join(s.text for s in segs if s.role == "subject")
    points = []
    for line in key_sentences(segs):
        points.append(f"ประเด็นจากคำบอกเล่า {line}")
    if not points:
        story = next((a for a in answers if len(a) > 40), answers[0] if answers else "")
        if story:
            points.append(f"เหตุการณ์โดยย่อ: {shorten(tidy(story.removeprefix('ข้าฯ ').strip()))}")
    for label, values in key_facts(" ".join(s.text for s in segs), subject_text).items():
        if values:
            points.append(f"{label}: {', '.join(values[:6])}")
    if not any(p.startswith("จำนวนเงิน") for p in points) and re.search(r"เงิน|โอน", subject_text):
        points.append("จำนวนเงิน: ผู้ให้การกล่าวถึงการโอนเงิน แต่ไม่ได้ระบุจำนวนที่ชัดเจน")
    wish = next((a for a in answers if re.search(r"ประสงค์|ดำเนินคดี|ติดตามเงิน", a)), "")
    if wish:
        points.append(f"ความประสงค์: {wish.removeprefix('ข้าฯ ').strip()}")
    return points


def spoken_sentence(text: str) -> str:
    """One stretch of the subject's speech: pronouns → ข้าฯ, fillers and particles removed, nothing added."""
    t = qa.to_official_answer(text.replace("\ufffd", ""))
    if SHORT_REPLY.match(t.strip()):
        return ""
    t = LEADING_REPLY.sub("ข้าฯ ", t)  # "ใช่ ข้าฯ เปิดบัญชี" → "ข้าฯ เปิดบัญชี": a bare yes/no has no meaning in a story
    # to_official_answer always starts with ข้าฯ; keep it only where the speaker used a pronoun
    if not qa.clean(text).startswith(("ผม", "ดิฉัน", "กระผม", "หนู", "ฉัน", "ข้าพเจ้า", "เรา")):
        t = t.removeprefix("ข้าฯ").strip()
    return t


def spoken_sentences(segs) -> list[str]:
    """The subject's own sentences in order (see spoken_sentence)."""
    return [t for t in (spoken_sentence(s.text) for s in segs if s.role == "subject") if t]


NUMBERISH = re.compile(r"[\d,.]+|" + NUMBER_WORDS.pattern)


def tidy(text: str) -> str:
    """Drop the stumbles of speech: a phrase said twice, or started again ("สำนอง สำนองจ่ายก่อน" → "สำนองจ่ายก่อน").

    Numbers are never touched: digits read one by one ("สอง สอง ศูนย์") are an account or phone number.
    """
    out: list[str] = []
    for chunk in (text or "").split():
        prev = out[-1] if out else ""
        if len(prev) >= 3 and not NUMBERISH.fullmatch(prev) and not NUMBERISH.fullmatch(chunk):
            if chunk == prev:
                continue
            if chunk.startswith(prev) or chunk.endswith(prev):
                out[-1] = chunk
                continue
        out.append(chunk)
    return " ".join(out)


PARA_PAUSE, PARA_MIN, PARA_MAX = 1.2, 150, 700  # seconds of silence · characters per paragraph


def code_narrative(pairs, segs) -> str:
    """คำให้การแบบบรรยาย: ข้าฯ ขอให้การว่า … then the subject's own words, with a new paragraph where they paused."""
    paras, cur, size, prev_end = [], [], 0, None
    for s in segs:
        if s.role != "subject":
            continue
        text = tidy(spoken_sentence(s.text))
        if not text:
            continue
        gap = s.start - prev_end if prev_end is not None else 0.0
        if cur and ((gap >= PARA_PAUSE and size >= PARA_MIN) or size >= PARA_MAX):
            paras.append(" ".join(cur))
            cur, size = [], 0
        cur.append(text)
        size += len(text)
        prev_end = s.end
    if cur:
        paras.append(" ".join(cur))
    if not paras:  # no subject segments (an imported transcript without speakers): use the answers
        answers = [tidy(a) for a in subject_answers(pairs, segs)]
        paras = [" ".join(answers[i : i + 3]) for i in range(0, len(answers), 3)] if answers else []
    if not paras:
        return ""
    paras[0] = "ข้าฯ ขอให้การว่า " + paras[0].removeprefix("ข้าฯ").strip()
    return "\n".join(paras)


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


QUESTION_MAX = 80  # characters: a longer stretch ending in "ไหม" is usually the complainant quoting someone


def guess_speaker(text: str) -> str:
    """S1 = investigator (questions, rights, procedure) · S2 = the person giving the statement.

    A complainant telling the story often quotes questions ("เขาบอกว่าสมัครเลยไหม"); taking those for the officer's
    dropped them from the story, so only short questions count as the officer's.
    """
    t = (text or "").strip()
    return "S1" if OFFICER_PHRASES.search(t) or (QUESTION.search(t) and len(t) <= QUESTION_MAX) else "S2"
