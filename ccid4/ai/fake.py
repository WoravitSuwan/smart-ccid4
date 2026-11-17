"""FakeLLM answers (spec 4: "ตอบค่าคงที่ตาม schema"). Built only from the given case data, never invents facts."""

import re

from core.thai import to_arabic_digits

CHARGE_RULES = [
    (("หลอก", "โอนเงิน", "ออนไลน์", "ลงทุน", "สินค้า", "บัญชีม้า"), {
        "offense": "ฉ้อโกงประชาชน และนำเข้าสู่ระบบคอมพิวเตอร์ซึ่งข้อมูลคอมพิวเตอร์อันเป็นเท็จ",
        "law_sections": "ป.อ. ม.343, พ.ร.บ.ว่าด้วยการกระทำความผิดเกี่ยวกับคอมพิวเตอร์ฯ ม.14(1)",
        "penalty_text": "จำคุกไม่เกิน 5 ปี หรือปรับไม่เกิน 100,000 บาท หรือทั้งจำทั้งปรับ",
        "max_penalty_months": 60, "max_fine": 100000, "court_type": "criminal"}),
    (("ข่มขู่", "กรรโชก", "แบล็กเมล์"), {
        "offense": "กรรโชกทรัพย์", "law_sections": "ป.อ. ม.337",
        "penalty_text": "จำคุกไม่เกิน 5 ปี และปรับไม่เกิน 100,000 บาท",
        "max_penalty_months": 60, "max_fine": 100000, "court_type": "provincial"}),
    (("หมิ่น", "ใส่ความ", "ด่าทอ"), {
        "offense": "หมิ่นประมาทโดยการโฆษณา", "law_sections": "ป.อ. ม.328",
        "penalty_text": "จำคุกไม่เกิน 2 ปี และปรับไม่เกิน 200,000 บาท",
        "max_penalty_months": 24, "max_fine": 200000, "court_type": "provincial"}),
    (("ขับรถ", "ชน", "ประมาท"), {
        "offense": "ขับรถโดยประมาทเป็นเหตุให้ผู้อื่นได้รับอันตราย", "law_sections": "ป.อ. ม.300",
        "penalty_text": "จำคุกไม่เกิน 3 ปี หรือปรับไม่เกิน 60,000 บาท หรือทั้งจำทั้งปรับ",
        "max_penalty_months": 36, "max_fine": 60000, "court_type": "magistrate"}),
]  # fmt: skip
DEFAULT_CHARGE = {
    "offense": "ฉ้อโกง", "law_sections": "ป.อ. ม.341",
    "penalty_text": "จำคุกไม่เกิน 3 ปี หรือปรับไม่เกิน 60,000 บาท หรือทั้งจำทั้งปรับ",
    "max_penalty_months": 36, "max_fine": 60000, "court_type": "magistrate",
}  # fmt: skip


def _all_text(ctx) -> str:
    case = ctx.get("คดี", {})
    acc = ctx.get("ผู้กล่าวหา") or {}
    return " ".join(str(x) for x in (case.get("เรื่อง"), case.get("พฤติการณ์"), case.get("ฐานความผิดเดิม"), acc.get("คำให้การ")) if x)


def analyze_charges(ctx, extra):
    text = _all_text(ctx)
    hit = next((r for kw, r in CHARGE_RULES if any(k in text for k in kw)), None)
    out = dict(hit or DEFAULT_CHARGE)
    place = ctx.get("คดี", {}).get("สถานที่เกิดเหตุ") or ""
    out["jurisdiction"] = f"ศาลที่มีเขตอำนาจเหนือท้องที่เกิดเหตุ ({place})" if place else "ศาลที่มีเขตอำนาจเหนือท้องที่เกิดเหตุ"
    out["reasoning"] = "พบถ้อยคำในข้อมูลคดีที่ตรงกับองค์ประกอบความผิดนี้" if hit else "ข้อมูลยังไม่ชัด เสนอฐานความผิดทั่วไปไว้ก่อน ควรตรวจทาน"
    return out


def summarize_case(ctx, extra):
    case = ctx.get("คดี", {})
    acc = ctx.get("ผู้กล่าวหา") or {}
    parts = []
    when = " ".join(
        x
        for x in (case.get("วันเกิดเหตุ") and f"เมื่อวันที่ {case['วันเกิดเหตุ']}", case.get("เวลาเกิดเหตุ") and f"เวลา {case['เวลาเกิดเหตุ']}")
        if x
    )
    where = f"ที่ {case['สถานที่เกิดเหตุ']}" if case.get("สถานที่เกิดเหตุ") else ""
    lead = " ".join(x for x in (when, where) if x)
    story = (acc.get("คำให้การ") or case.get("พฤติการณ์") or "").strip()
    if lead:
        parts.append(f"{lead} {story}".strip())
    elif story:
        parts.append(story)
    if case.get("ความเสียหาย"):
        parts.append(f"ผู้กล่าวหาได้รับความเสียหายรวมเป็นเงิน {case['ความเสียหาย']} บาท")
    if acc.get("ชื่อ") and case.get("วันรับแจ้ง"):
        parts.append(f"{acc['ชื่อ']} จึงมาร้องทุกข์ต่อพนักงานสอบสวนเมื่อวันที่ {case['วันรับแจ้ง']} เพื่อดำเนินคดีตามกฎหมาย")
    return {"summary": "\n".join(parts) or "ไม่ปรากฏในข้อมูล"}


def draft_statement(ctx, extra):
    role, plea = extra.get("role"), extra.get("plea")
    case = ctx.get("คดี", {})
    story = (case.get("พฤติการณ์") or "").strip() or "……"
    if role == "accuser":
        text = (f"ข้าฯ เป็นผู้เสียหายในคดีนี้ ขอให้การว่า {story}\n"
                + (f"ข้าฯ ได้รับความเสียหายเป็นเงิน {case['ความเสียหาย']} บาท\n" if case.get("ความเสียหาย") else "")
                + "ข้าฯ ประสงค์ให้ดำเนินคดีกับผู้กระทำความผิดจนถึงที่สุด")  # fmt: skip
    elif role == "witness":
        text = f"ข้าฯ มาให้การในฐานะพยาน เกี่ยวกับเหตุที่ {story}\nข้าฯ รู้เห็นเหตุการณ์ดังนี้ ……"
    elif plea == "confess":
        text = f"ข้าฯ ขอให้การรับสารภาพตลอดข้อกล่าวหา ว่า{case.get('ฐานความผิดเดิม') or ''}\nข้อเท็จจริงคือ {story}"
    elif plea == "later":
        text = "ข้าฯ ขอให้การปฏิเสธในชั้นพนักงานสอบสวน และจะให้การในชั้นศาล"
    else:
        text = "ข้าฯ ขอให้การปฏิเสธตลอดข้อกล่าวหา ข้าฯ ไม่ได้กระทำความผิดตามที่ถูกกล่าวหา ……"
    return {"text": text, "notes": ["ร่างจากข้อมูลในระบบ ต้องตรวจและแก้ให้ตรงกับถ้อยคำจริง"]}


NUM = re.compile(r"\d[\d,\.]*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(to_arabic_digits(text or ""))}


def check_consistency(ctx, extra):
    statement = ctx.get("คำให้การ", "")
    known = _numbers(ctx.get("พฤติการณ์", "")) | _numbers(" ".join(str(v) for v in ctx.get("ข้อเท็จจริง", {}).values()))
    flags = []
    for sentence in re.split(r"[\n ]{2,}|\n", statement):
        for n in _numbers(sentence) - known:
            flags.append({"quote": sentence.strip()[:160], "issue": f"ตัวเลข {n} ไม่พบในพฤติการณ์แห่งคดี"})
    return {"flags": flags}


# ---------------------------------------------------------------- extraction (phase 6)
TITLES = (
    "นางสาว",
    "นาง",
    "นาย",
    "ด.ช.",
    "ด.ญ.",
    "เด็กชาย",
    "เด็กหญิง",
    "ร.ต.อ.",
    "ร.ต.ท.",
    "ร.ต.ต.",
    "ด.ต.",
    "จ.ส.ต.",
    "ส.ต.อ.",
    "พ.ต.ต.",
    "พ.ต.ท.",
    "พ.ต.อ.",
)
PHONE = re.compile(r"0\d[\d\- ]{7,10}\d")
CID = re.compile(r"\b\d[\d\- ]{11,16}\d\b")
ACCOUNT = re.compile(r"\d{3}[\-\s]?\d[\-\s]?\d{5}[\-\s]?\d|\d{10,12}")
BANKS = (
    "กสิกรไทย",
    "กรุงเทพ",
    "กรุงไทย",
    "ไทยพาณิชย์",
    "กรุงศรี",
    "ทหารไทยธนชาต",
    "ttb",
    "ออมสิน",
    "ธ.ก.ส.",
    "ยูโอบี",
    "ซีไอเอ็มบี",
    "เกียรตินาคิน",
    "พร้อมเพย์",
)


def split_name(text: str) -> dict:
    text = re.sub(r"\s+", " ", (text or "").strip())
    title = next((t for t in TITLES if text.startswith(t)), "")
    rest = text[len(title) :].strip()
    first, _, last = rest.partition(" ")
    return {"title": title, "first_name": first, "last_name": last.split(" ")[0] if last else ""}


def _money(text: str) -> float:
    nums = [
        float(n.replace(",", ""))
        for n in re.findall(r"\d[\d,]*(?:\.\d+)?", to_arabic_digits(text or ""))
        if len(n.replace(",", "")) <= 9
    ]
    return max(nums) if nums else 0.0


def _people(text: str) -> list[dict]:
    out = []
    for line in re.split(r"[\n,;]| และ ", text or ""):
        line = line.strip(" -•")
        if not line or line in ("ไม่มี", "ไม่ทราบ"):
            continue
        phone = PHONE.search(line)
        name_part = PHONE.sub("", line).replace("เบอร์", "").replace("โทร", "").strip(" :")
        p = split_name(name_part[:60]) if name_part else {"title": "", "first_name": "ไม่ทราบชื่อ", "last_name": ""}
        out.append(p | {"phone": phone.group(0).replace(" ", "").replace("-", "") if phone else "", "detail": line[:300]})
    return out


def _empty_case() -> dict:
    return {"title": "", "offense": "", "law_sections": "", "circumstances": "", "incident_at": "", "location": "", "damage_amount": 0,
            "accuser": None, "suspects": [], "witnesses": [], "properties": [], "accounts": [], "arrest_place": "", "arrest_team": []}  # fmt: skip


def extract_intake(ctx, extra):
    """ctx = the report as '[หัวข้อ] ข้อความ' lines (intake.interview.transcript_text); extra['answers'] has the raw answers."""
    a = extra.get("answers", {})

    def txt(key, field=None):
        """A form field when given (form mode), otherwise the free-text chat answer for that topic."""
        topic = a.get(key) or {}
        if field:
            return str(topic.get(field) or "")
        return topic.get("text", "")

    out = _empty_case()
    rep = txt("reporter", "name") or txt("reporter")
    phone = txt("reporter", "phone") or (PHONE.search(rep).group(0) if PHONE.search(rep) else "")
    name_line = PHONE.sub("", rep).split("\n")[0].split(" ที่อยู่")[0]
    acc = split_name(" ".join(name_line.split()[:3]))
    address = txt("reporter", "address") or (rep.split("ที่อยู่", 1)[1].strip(" :") if "ที่อยู่" in rep else "")
    out["accuser"] = acc | {
        "phone": phone.replace("-", "").replace(" ", ""),
        "address": address,
        "occupation": txt("reporter", "occupation") or "",
    }
    story = txt("story", "story") or txt("story")
    out["circumstances"] = f"ผู้แจ้งแจ้งว่า {story}".strip() if story else ""
    out["title"] = (story or "")[:120]
    when = txt("when_where", "when")
    out["incident_at"] = when if when and re.match(r"\d{4}-\d{2}-\d{2}", when) else ""
    out["location"] = txt("when_where", "where") or txt("when_where")
    loss = " ".join(dict.fromkeys(x for x in (txt("loss", "items"), txt("loss", "amount"), txt("loss")) if x))
    amount = txt("loss", "amount")
    out["damage_amount"] = float(amount) if re.fullmatch(r"\d+(\.\d+)?", amount or "") else _money(loss)
    if loss.strip():
        out["properties"] = [{"name": (txt("loss", "items") or txt("loss") or "เงินที่เสียหาย")[:200], "value": out["damage_amount"]}]
    acc_text = txt("accounts", "accounts") or txt("accounts")
    for line in acc_text.split("\n"):
        m = ACCOUNT.search(line)
        if m:
            bank = next((b for b in BANKS if b in line), "")
            out["accounts"].append(
                {
                    "bank": bank,
                    "account_no": re.sub(r"\D", "", m.group(0)),
                    "account_name": "",
                    "amount": _money(line.replace(m.group(0), "")),
                    "at": "",
                }
            )
    out["suspects"] = _people(txt("suspects", "suspects") or txt("suspects"))
    out["witnesses"] = [
        {k: p[k] for k in ("title", "first_name", "last_name", "phone")}
        for p in _people(txt("witnesses", "witnesses") or txt("witnesses"))
    ]
    text = f"{story} {acc_text}"
    out["offense"] = analyze_charges({"คดี": {"พฤติการณ์": text}}, {})["offense"] if story else ""
    return out


LABEL = re.compile(r"^\s*([^:：\n]{2,40})\s*[:：]\s*(.+)$", re.M)


def _labels(text: str) -> dict[str, str]:
    """'หัวข้อ: ค่า' lines; a value continues on the following lines until the next 'หัวข้อ:'."""
    out, key = {}, None
    for line in (text or "").splitlines():
        m = LABEL.match(line)
        if m:
            key = m.group(1).strip()
            out[key] = m.group(2).strip()
        elif key and line.strip():
            out[key] = f"{out[key]} {line.strip()}"
    return out


def _thai_datetime(text: str) -> str:
    """'15 พฤศจิกายน 2569 เวลา 22.30 น.' → '2026-11-15T22:30'."""
    from core.thai import MONTHS

    t = to_arabic_digits(text or "")
    m = re.search(r"(\d{1,2})\s*(" + "|".join(MONTHS) + r")\s*(?:พ\.ศ\.\s*)?(\d{4})", t)
    if not m:
        return ""
    day, month, year = int(m.group(1)), MONTHS.index(m.group(2)) + 1, int(m.group(3)) - 543
    hm = re.search(r"(\d{1,2})[.:](\d{2})\s*น", t)
    hh, mm = (int(hm.group(1)), int(hm.group(2))) if hm else (0, 0)
    return f"{year:04d}-{month:02d}-{day:02d}T{hh:02d}:{mm:02d}"


def extract_arrest(ctx, extra):
    L = _labels(ctx)
    get = lambda *keys: next((L[k] for k in keys if L.get(k)), "")  # noqa: E731
    out = _empty_case()
    name = get("ผู้ต้องหา", "ชื่อผู้ต้องหา", "ผู้ถูกจับ")
    if name:
        cid = CID.search(get("เลขประจำตัวประชาชน") or "")
        out["suspects"] = [split_name(name) | {
            "citizen_id": re.sub(r"\D", "", cid.group(0)) if cid else "", "address": get("ที่อยู่ผู้ต้องหา", "ที่อยู่"),
            "marks": get("ตำหนิรูปพรรณ"), "arrested_at": _thai_datetime(get("วันเวลาที่จับกุม", "วันที่จับกุม")), "phone": "", "detail": ""}]  # fmt: skip
    out["offense"] = get("ข้อหา", "ข้อกล่าวหา")
    out["law_sections"] = get("ฐานความผิดตามกฎหมาย", "มาตรา")
    out["arrest_place"] = get("สถานที่จับกุม")
    out["location"] = out["arrest_place"]
    out["incident_at"] = _thai_datetime(get("วันเวลาที่จับกุม", "วันที่จับกุม"))
    out["circumstances"] = get("พฤติการณ์การจับกุม", "พฤติการณ์")
    team = [x.strip() for x in re.split(r"[,、]| และ ", get("เจ้าหน้าที่ผู้จับกุม", "ชุดจับกุม")) if x.strip()]
    out["arrest_team"] = team
    if team:
        out["accuser"] = split_name(team[0]) | {"phone": "", "address": "", "occupation": "เจ้าพนักงานตำรวจ"}
    ex = get("ของกลาง")
    if ex and ex not in ("ไม่มี", "-"):
        out["properties"] = [{"name": x.strip(), "value": 0} for x in re.split(r"[,;]", ex) if x.strip()]
    out["title"] = f"จับกุม {name} ข้อหา {out['offense']}".strip() if name else out["offense"]
    return out
