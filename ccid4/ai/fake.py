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
