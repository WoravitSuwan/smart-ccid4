# smart-ccid4

CCID4 ระบบช่วยพนักงานสอบสวน กก.1 บก.สอท.4 (Django) อยู่ในโฟลเดอร์ [`ccid4/`](ccid4/)

- สเปก: `ccid4/docs/SPEC_CCID4.md` · ความคืบหน้า: `ccid4/docs/PROGRESS.md`
- **ทดลองใช้งานจริงบนเครื่องเดียว (อัดเสียงผู้แจ้งความ → คำให้การ Word):** `ccid4/docs/ติดตั้งบนเครื่องเพื่อทดสอบ.md`
- ติดตั้งบนเซิร์ฟเวอร์ของหน่วย: `cd ccid4 && cp .env.example .env && make up && make seed`
- ดูหน้าตาบน Vercel (ข้อมูลจำลองเท่านั้น): `ccid4/docs/VERCEL_PREVIEW.md`
- `design/` แบบหน้าจอ · `legacy/fastapi-phase1/` ระบบรุ่นแรก (จะลบเมื่อ Django ทำได้ครบ)
