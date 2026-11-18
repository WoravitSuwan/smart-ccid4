#!/usr/bin/env bash
# ติดตั้ง CCID4 บนเครื่องนี้เพื่อทดลองใช้งานจริง (macOS / Linux / Git Bash บน Windows)
#   bash scripts/install_local.sh                 # โมเดลถอดเสียง typhoon-turbo (ภาษาไทยโดยเฉพาะ เร็ว เหมาะกับ CPU)
#   bash scripts/install_local.sh typhoon-large   # แม่นกว่า ช้ากว่า 3–4 เท่า
# รันซ้ำได้ ไม่ลบข้อมูลเดิม
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${1:-typhoon-turbo}"
DC=(docker compose -f docker-compose.local.yml)

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
command -v docker >/dev/null || { echo "ยังไม่มี Docker — ติดตั้ง Docker Desktop (docker.com) แล้วรันคำสั่งนี้ใหม่"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker ยังไม่ทำงาน — เปิด Docker Desktop รอจนขึ้น Engine running แล้วรันใหม่"; exit 1; }

if [ ! -f .env ]; then
  say "สร้างไฟล์ตั้งค่า .env (รหัสสุ่มใหม่ เก็บในเครื่องนี้เท่านั้น)"
  PW=$(openssl rand -hex 16); KEY=$(openssl rand -hex 32)
  cat > .env <<EOF
DJANGO_SECRET_KEY=$KEY
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
POSTGRES_PASSWORD=$PW
DATABASE_URL=postgres://ccid4:$PW@db:5432/ccid4
REDIS_URL=redis://redis:6379/0
CCID4_DEMO_MODE=false
AI_BACKEND=fake
ASR_BACKEND=whisper
ASR_MODEL=
ASR_DEVICE=cpu
ASR_COMPUTE_TYPE=int8
ASR_BEAM_SIZE=0
SESSION_IDLE_SECONDS=3600
TZ=Asia/Bangkok
EOF
fi

say "สร้างโปรแกรม (ครั้งแรกประมาณ 10–20 นาที ขึ้นกับอินเทอร์เน็ต)"
"${DC[@]}" build

say "เตรียมโมเดลถอดเสียง $MODEL (ครั้งแรก: ดาวน์โหลดและแปลง ประมาณ 2–4 GB ใช้ 15–30 นาที · ครั้งต่อไปข้ามได้ทันที)"
"${DC[@]}" stop worker >/dev/null 2>&1 || true  # free memory for the one-time conversion
"${DC[@]}" up -d db redis
OUT=$("${DC[@]}" run --rm -T -e HF_HUB_OFFLINE=0 worker python manage.py setup_asr --model "$MODEL" | tee /dev/stderr)
MODEL_PATH=$(printf '%s\n' "$OUT" | sed -n 's/^ASR_MODEL=//p' | tail -1 | tr -d '\r')
[ -n "$MODEL_PATH" ] || { echo "โหลดโมเดลไม่สำเร็จ ดูข้อความด้านบน"; exit 1; }
grep -v '^ASR_MODEL=' .env > .env.tmp && echo "ASR_MODEL=$MODEL_PATH" >> .env.tmp && mv .env.tmp .env
if grep -qx 'ASR_BEAM_SIZE=5' .env; then  # value written by older installers: slow on a laptop CPU
  echo "ปรับ ASR_BEAM_SIZE จาก 5 เป็น 0 (อัตโนมัติ) ใน .env — บน CPU ถอดเสียงเร็วขึ้นราว 2 เท่า (ต้องการแม่นสุดแก้กลับเป็น 5 ได้)"
  sed 's/^ASR_BEAM_SIZE=5$/ASR_BEAM_SIZE=0/' .env > .env.tmp && mv .env.tmp .env
fi

say "เปิดระบบ"
"${DC[@]}" up -d
for _ in $(seq 1 90); do curl -fsS -o /dev/null http://localhost:8000/accounts/login/ 2>/dev/null && break; sleep 2; done

if [ "$("${DC[@]}" exec -T web python manage.py shell -c 'from accounts.models import User; print(User.objects.count())' | tr -d '\r')" = "0" ]; then
  say "สร้างบัญชีพนักงานสอบสวนคนแรก"
  "${DC[@]}" exec web python manage.py create_officer
fi

say "เสร็จแล้ว — เปิด http://localhost:8000 ด้วย Chrome หรือ Edge บนเครื่องนี้"
echo "หยุดระบบ:  docker compose -f docker-compose.local.yml stop      เปิดอีกครั้ง:  docker compose -f docker-compose.local.yml up -d"
