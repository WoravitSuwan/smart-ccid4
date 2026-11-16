"""แบบหนังสือออกภายนอก (A4.6). Each template lists only the fields it needs; the document text comes in phase 4."""

LETTER_TEMPLATES = {
    "cctv": {
        "label": "ขอภาพกล้องวงจรปิด (CCTV)",
        "subject": "ขอความอนุเคราะห์ภาพจากกล้องวงจรปิด",
        "recipient": "เจ้าของหรือผู้ดูแลกล้องวงจรปิด",
        "fields": [
            ("place", "สถานที่ติดตั้งกล้อง", "text"),
            ("from_at", "ช่วงเวลาที่ขอ ตั้งแต่", "datetime-local"),
            ("to_at", "ถึง", "datetime-local"),
            ("purpose", "เหตุที่ขอ", "textarea"),
        ],
    },
    "bank": {
        "label": "ขอข้อมูลบัญชีธนาคาร",
        "subject": "ขอข้อมูลบัญชีเงินฝากและรายการเดินบัญชี",
        "recipient": "ผู้จัดการธนาคาร",
        "fields": [
            ("bank", "ธนาคาร", "text"),
            ("account_no", "เลขบัญชี", "text"),
            ("account_name", "ชื่อบัญชี", "text"),
            ("from_date", "รายการเดินบัญชี ตั้งแต่วันที่", "date"),
            ("to_date", "ถึงวันที่", "date"),
            ("freeze", "ขอให้ระงับการทำธุรกรรมด้วย", "checkbox"),
        ],
    },
    "telco": {
        "label": "ขอข้อมูลผู้ให้บริการโทรศัพท์",
        "subject": "ขอข้อมูลผู้ใช้บริการและประวัติการใช้งานหมายเลขโทรศัพท์",
        "recipient": "ผู้ให้บริการโทรศัพท์เคลื่อนที่",
        "fields": [
            ("provider", "ผู้ให้บริการ", "text"),
            ("phone", "หมายเลขโทรศัพท์", "text"),
            ("from_date", "ช่วงวันที่ ตั้งแต่", "date"),
            ("to_date", "ถึง", "date"),
            ("items", "ข้อมูลที่ขอ (ผู้จดทะเบียน, CDR, cell site ฯลฯ)", "textarea"),
        ],
    },
    "platform": {
        "label": "ขอข้อมูลแพลตฟอร์มออนไลน์",
        "subject": "ขอข้อมูลผู้ใช้งานบัญชีบนแพลตฟอร์ม",
        "recipient": "ผู้ให้บริการแพลตฟอร์ม",
        "fields": [
            ("platform", "แพลตฟอร์ม", "text"),
            ("account", "บัญชี/ลิงก์/ไอดี", "text"),
            ("from_date", "ช่วงวันที่ ตั้งแต่", "date"),
            ("to_date", "ถึง", "date"),
            ("items", "ข้อมูลที่ขอ (ผู้สมัคร, IP log ฯลฯ)", "textarea"),
        ],
    },
}
