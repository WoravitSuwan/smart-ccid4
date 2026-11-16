# ดูหน้าตา CCID4 บน Vercel (โหมดสาธิต)

ใช้ดูหน้าจอก่อนติดตั้งจริงเท่านั้น **ห้ามใส่ข้อมูลคดีหรือข้อมูลบุคคลจริง** เพราะ Vercel และผู้ให้บริการฐานข้อมูลอยู่นอกเครือข่ายของหน่วย
การใช้งานจริงติดตั้งด้วย `make up` บนเครื่องของหน่วย (ดู `docs/OPERATIONS.md` ในเฟส 12)

## ตั้งค่าครั้งแรก
1. Vercel → Add New → Project → เลือก repo นี้ · **Root Directory ให้เป็นรากของ repo** (ไม่ใช่ `ccid4/`) · Framework Preset: Other · ไม่ต้องตั้ง Build Command
2. ในโปรเจกต์ → Storage → Create Database → **Neon (Postgres)** → Connect กับโปรเจกต์ (Vercel ใส่ `DATABASE_URL` ให้เอง) · Neon รองรับส่วนขยาย `vector` และ `pg_trgm` ที่ระบบต้องใช้
3. Settings → Environment Variables → เพิ่ม `DJANGO_SECRET_KEY` = ข้อความสุ่มยาว 50 ตัวอักษรขึ้นไป
4. Deployments → Redeploy
5. เปิดเว็บครั้งแรกจะช้า (10–40 วินาที) เพราะระบบสร้างตารางและใส่ข้อมูลจำลองให้ (ผู้ใช้ 6 บทบาท คดี 30 คดี วันหยุด ฎีกาจำลอง) ครั้งต่อไปเร็วปกติ

เข้าระบบด้วย `investigator1` / `ccid4-demo-2569` (รายชื่อบัญชีทดลองอยู่ใต้ช่องรหัสผ่าน)

## สิ่งที่ต่างจากเครื่องจริง
| เรื่อง | บน Vercel | บนเครื่องของหน่วย |
|---|---|---|
| AI | โหมดจำลอง (FakeLLM) | Ollama บนเครื่อง GPU ของหน่วย |
| PDF | ไม่มี LibreOffice จึงออกได้เฉพาะ Word | Word และ PDF |
| งานเบื้องหลัง | ทำในคำขอเดียว | Celery worker |
| ไฟล์ที่ออก | เก็บชั่วคราว หายเมื่อ Vercel รีไซเคิลเครื่อง | เก็บถาวรในโฟลเดอร์ข้อมูล |
| ข้อมูล | จำลองเท่านั้น (ป้าย "ข้อมูลจำลอง" ตลอด) | ข้อมูลจริง |

## ไฟล์ที่เกี่ยวข้อง
`vercel.json`, `api/index.py`, `requirements.txt`, `.vercelignore` ที่รากของ repo · `ccid4/config/settings/vercel.py` · `ccid4/core/preview.py`
ถ้ายังไม่ได้ตั้งฐานข้อมูลหรือ secret หน้าเว็บจะบอกขั้นตอนที่ขาด
