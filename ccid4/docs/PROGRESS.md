# PROGRESS

## การตัดสินใจที่ผู้ใช้อนุมัติ (16 พ.ย. 2569: "ทำเองเลยครับทุกเฟส")
1. โปรเจกต์ Django อยู่ที่ `ccid4/` · ระบบ FastAPI ระยะที่ 1 ย้ายไป `legacy/fastapi-phase1/` จนกว่า Django จะทำได้เท่ากัน (เฟส 7–8) แล้วจึงลบ
2. เลิกใช้ Vercel · ดูระบบด้วย `make up` บนเครื่องของหน่วย
3. ฟอนต์ TH SarabunIT๙ ยังไม่มีไฟล์ · ระหว่างนี้ใช้ Laksaman (fonts-thai-tlwg) แทนผ่าน `deploy/fonts.conf` · วางไฟล์ .ttf ใน `fonts/` แล้ว build ใหม่ ระบบจะใช้ฟอนต์จริง
4. LibreOffice ติดตั้งใน image ของ worker (ไม่แยก container)
5. เพิ่ม token 3 ตัวเพื่อให้ผ่าน WCAG AA: `--on-primary` (ตัวอักษรบนปุ่มหลัก), `--warning-ink`, `--accent-ink` (ตัวอักษรเตือน/เน้นในธีมสว่าง) · `--warning`/`--accent` เดิมใช้กับเส้นขอบและไอคอน
6. ไฟล์ `docs/reference/MetaCops_สรุปการใช้งาน.pdf` ไม่ commit (เป็นเอกสารของบริษัทอื่น) เก็บไว้ในเครื่องเท่านั้น

## เฟส 0 — เสร็จ
- โครง Django 5 + settings แยก dev/test/prod, Celery, docker compose (web, worker, beat, db pgvector pg16, redis, proxy Caddy HTTPS ภายใน, ollama ใน profile `ai`)
- design tokens ฟ้า–ดำ ทั้งธีมมืด (ค่าเริ่มต้น) และสว่าง ผูก Tailwind (self-host ทุกไฟล์: HTMX, Alpine CSP build, IBM Plex Sans Thai, wavesurfer, cytoscape)
- base layout: แถบบน (ค้นหา กระดิ่ง สลับธีม เมนูผู้ใช้) + เมนูซ้ายพับได้ 10 เมนู (เมนูที่ยังไม่ทำแสดง "เร็ว ๆ นี้")
- โลโก้ SVG ของ CCID4 (`static/brand/ccid4.svg`)
- ผู้ใช้ 6 บทบาท + ตารางสิทธิ์ (`accounts/models.py: PERMISSIONS`), Argon2, ล็อกเมื่อผิด 5 ครั้ง 30 นาที (django-axes), บัญชีมีวันหมดอายุ, TOTP 2FA (บังคับสำหรับผู้ดูแลระบบ และเปิดบังคับรายคนได้), session หมดอายุเมื่อไม่ใช้ 30 นาที, ธีมจำค่าต่อผู้ใช้
- AuditLog: เพิ่มอย่างเดียว ผูกแฮชกับรายการก่อนหน้า, trigger ใน PostgreSQL ห้าม UPDATE/DELETE/TRUNCATE, บันทึกเข้า/ออก/ผิดรหัส/ล็อก/2FA, หน้า `/audit/`
- `seed_demo` ผู้ใช้ปลอมทุกบทบาท (Faker th_TH) รหัสผ่าน `ccid4-demo-2569`
- `/styleguide` แสดงทั้งสองธีมเทียบกัน
- เทสต์ 19 ข้อ: สิทธิ์, ล็อก, หมดอายุ, 2FA, ธีม, audit chain + trigger + ตรวจจับการแก้, contrast AA จาก tokens.css, ไม่มี URL ภายนอกในเทมเพลต

### วิธีทดสอบด้วยมือ
1. `cp .env.example .env` แก้ค่า แล้ว `make up` และ `make seed`
2. เปิด `https://ccid4.local` เข้าด้วย `investigator1` / `ccid4-demo-2569`
3. กดปุ่มดวงอาทิตย์สลับธีม แล้วรีเฟรช ธีมต้องคงเดิม · กด "ย่อเมนู"
4. ออกจากระบบ ใส่รหัสผิด 5 ครั้ง ต้องเห็นหน้า "บัญชีถูกล็อกชั่วคราว"
5. เข้าด้วย `admin1` ต้องถูกพาไปตั้ง 2FA ก่อน · `supervisor1` เปิด `/audit/` เห็นประวัติและป้ายตรวจความต่อเนื่อง

### ค้าง / ข้อจำกัด
- เครื่องที่พัฒนาไม่มี Docker daemon: ยังไม่ได้ build image จริง (เทสต์รันกับ PostgreSQL 16 + pgvector ที่ติดตั้งในเครื่อง)
- ค้นหาทั้งระบบและกระดิ่งแจ้งเตือนยังเป็นปุ่มว่าง (เฟส 9 และ 11)
