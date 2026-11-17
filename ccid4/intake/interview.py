"""Topics the assistant asks about, one at a time (A2.2). The same list drives the chat and the step-by-step form.

The questions are fixed text written by the unit (not generated), so the citizen always gets the same clear,
polite questions; AI is used afterwards to turn the answers into case data (ai/prompts/extract_intake.txt).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"  # text | textarea | tel | date | datetime-local | number
    required: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    question: str  # what the assistant asks in chat mode
    fields: tuple = field(default_factory=tuple)
    kinds: tuple = ("criminal", "online")
    optional: bool = False  # the citizen may answer "ไม่มี" / skip

    def for_kind(self, kind: str) -> bool:
        return kind in self.kinds


TOPICS = (
    Topic("reporter", "ตัวผู้แจ้ง", "สวัสดีครับ ผมเป็นผู้ช่วยรับแจ้งความของสถานีตำรวจ ขอทราบชื่อ-นามสกุล เบอร์โทรศัพท์ และที่อยู่ที่ติดต่อได้ของท่านครับ", (
        Field("name", "ชื่อ-นามสกุล", required=True, placeholder="เช่น นายสมชาย ใจดี"),
        Field("phone", "เบอร์โทรศัพท์", "tel", required=True),
        Field("address", "ที่อยู่ที่ติดต่อได้", "textarea"),
        Field("occupation", "อาชีพ"),
    )),
    Topic("story", "เหตุการณ์", "เล่าเหตุการณ์ที่เกิดขึ้นให้ฟังได้เลยครับ เล่าตามลำดับว่าเกิดอะไรขึ้นบ้าง", (
        Field("story", "เล่าเหตุการณ์", "textarea", required=True),
    )),
    Topic("when_where", "วันเวลาและสถานที่", "เหตุเกิดเมื่อไร และที่ไหนครับ (ถ้าเป็นทางออนไลน์ บอกช่องทาง เช่น เฟซบุ๊ก ไลน์ เว็บไซต์)", (
        Field("when", "วันเวลาที่เกิดเหตุ", "datetime-local"),
        Field("where", "สถานที่หรือช่องทางออนไลน์"),
    )),
    Topic("loss", "ทรัพย์หรือเงินที่เสียหาย", "มีทรัพย์สินหรือเงินที่เสียหายไหมครับ รวมแล้วประมาณเท่าไร", (
        Field("items", "รายการทรัพย์/เงินที่เสียหาย", "textarea"),
        Field("amount", "มูลค่ารวม (บาท)", "number"),
    ), optional=True),
    Topic("accounts", "บัญชีที่โอนเงินไป", "ท่านโอนเงินไปบัญชีไหนบ้างครับ บอกธนาคาร เลขบัญชี ชื่อบัญชี วันเวลาและจำนวนที่โอน (แนบสลิปได้ในขั้นสุดท้าย)", (
        Field("accounts", "ธนาคาร เลขบัญชี ชื่อบัญชี วันเวลาและจำนวนที่โอน", "textarea"),
    ), kinds=("online",), optional=True),
    Topic("suspects", "ผู้ต้องสงสัย", "ท่านทราบข้อมูลของคนที่กระทำไหมครับ เช่น ชื่อ เบอร์โทร ชื่อบัญชีโซเชียล หรือรูปพรรณ", (
        Field("suspects", "ข้อมูลผู้ต้องสงสัย", "textarea"),
    ), optional=True),
    Topic("witnesses", "พยาน", "มีใครรู้เห็นเหตุการณ์บ้างไหมครับ ถ้ามี ขอชื่อและเบอร์ติดต่อ", (
        Field("witnesses", "ชื่อและเบอร์ติดต่อของพยาน", "textarea"),
    ), optional=True),
)  # fmt: skip

SKIP_WORDS = ("ไม่มี", "ไม่ทราบ", "ไม่รู้", "-", "ข้าม")


def topics_for(kind: str) -> list[Topic]:
    return [t for t in TOPICS if t.for_kind(kind)]


def answers_from_chat(kind: str, messages: list[dict]) -> dict:
    """Chat answers keyed by topic: {"story": {"text": "..."}} (fields are filled later by AI extraction)."""
    out = {}
    for m in messages:
        if m.get("role") == "user" and m.get("topic"):
            text = m["text"].strip()
            if text and text not in SKIP_WORDS:
                prev = out.get(m["topic"], {}).get("text", "")
                out[m["topic"]] = {"text": f"{prev}\n{text}".strip()}
    return out


def transcript_text(kind: str, answers: dict) -> str:
    """Plain text of the report for AI extraction and for officers."""
    lines = []
    for t in topics_for(kind):
        a = answers.get(t.key) or {}
        if "text" in a:
            lines.append(f"[{t.title}] {a['text']}")
        else:
            vals = [f"{f.label}: {a.get(f.key)}" for f in t.fields if a.get(f.key) not in (None, "")]
            if vals:
                lines.append(f"[{t.title}] " + " · ".join(vals))
    return "\n".join(lines)


PDPA_TEXT = (
    "ข้าพเจ้ายินยอมให้สถานีตำรวจเก็บ ใช้ และประมวลผลข้อมูลส่วนบุคคลที่ให้ไว้ รวมถึงไฟล์แนบ เพื่อการรับคำร้องทุกข์ "
    "การสืบสวนสอบสวน และการติดต่อกลับ ตามพระราชบัญญัติคุ้มครองข้อมูลส่วนบุคคล พ.ศ. 2562 ข้อมูลจัดเก็บและประมวลผล "
    "บนระบบของหน่วยงานเท่านั้น ข้าพเจ้ารับรองว่าข้อมูลเป็นความจริง และทราบว่าการแจ้งความเท็จมีความผิดตามกฎหมาย"
)
PDPA_STAFF_TEXT = "ผู้แจ้งให้ความยินยอมด้วยวาจาต่อหน้าเจ้าหน้าที่ตามข้อความยินยอมของหน่วย และเจ้าหน้าที่กรอกข้อมูลแทนตามคำบอกเล่า"
