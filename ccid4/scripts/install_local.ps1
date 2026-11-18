# ติดตั้ง CCID4 บนเครื่อง Windows นี้เพื่อทดลองใช้งานจริง
#   คลิกขวาที่เมนู Start > Terminal (หรือ PowerShell) แล้วพิมพ์:
#   cd โฟลเดอร์ที่แตกไฟล์ ccid4 ไว้
#   powershell -ExecutionPolicy Bypass -File scripts\install_local.ps1
#   (เติม large-v3 ท้ายคำสั่งถ้าต้องการโมเดลที่แม่นกว่าแต่ช้ากว่า)
# รันซ้ำได้ ไม่ลบข้อมูลเดิม
param([string]$Model = "large-v3-turbo")
$ErrorActionPreference = "Continue"  # native docker output on stderr must not stop the script; exit codes are checked
Set-Location (Join-Path $PSScriptRoot "..")
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Compose = @("compose", "-f", "docker-compose.local.yml")

function Say($text) { Write-Host ""; Write-Host "> $text" -ForegroundColor Cyan }
function Dc { & docker @Compose @args; if ($LASTEXITCODE -ne 0) { throw "คำสั่ง docker compose $args ไม่สำเร็จ" } }
function RandHex([int]$n) {
  $b = New-Object byte[] $n
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  ($b | ForEach-Object { $_.ToString("x2") }) -join ""
}
function WriteLF($path, $lines) {
  [System.IO.File]::WriteAllText((Join-Path (Get-Location) $path), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding $false))
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host "ยังไม่มี Docker - ติดตั้ง Docker Desktop (docker.com) แล้วรันคำสั่งนี้ใหม่"; exit 1 }
& docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker ยังไม่ทำงาน - เปิด Docker Desktop รอจนขึ้น Engine running แล้วรันใหม่"; exit 1 }

if (-not (Test-Path .env)) {
  Say "สร้างไฟล์ตั้งค่า .env (รหัสสุ่มใหม่ เก็บในเครื่องนี้เท่านั้น)"
  $pw = RandHex 16; $key = RandHex 32
  WriteLF ".env" @(
    "DJANGO_SECRET_KEY=$key",
    "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1",
    "DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000",
    "POSTGRES_PASSWORD=$pw",
    "DATABASE_URL=postgres://ccid4:$pw@db:5432/ccid4",
    "REDIS_URL=redis://redis:6379/0",
    "CCID4_DEMO_MODE=false",
    "AI_BACKEND=fake",
    "ASR_BACKEND=whisper",
    "ASR_MODEL=",
    "ASR_DEVICE=cpu",
    "ASR_COMPUTE_TYPE=int8",
    "ASR_BEAM_SIZE=0",
    "SESSION_IDLE_SECONDS=3600",
    "TZ=Asia/Bangkok"
  )
}

Say "สร้างโปรแกรม (ครั้งแรกประมาณ 10-20 นาที ขึ้นกับอินเทอร์เน็ต)"
Dc build

Say "ดาวน์โหลดโมเดลถอดเสียง $Model (ครั้งเดียว ประมาณ 1-3 GB)"
Dc up -d db redis
$out = & docker @Compose run --rm -T -e HF_HUB_OFFLINE=0 worker python manage.py setup_asr --model $Model
$out | ForEach-Object { Write-Host $_ }
$modelPath = ($out | Where-Object { $_ -match '^ASR_MODEL=' } | Select-Object -Last 1) -replace '^ASR_MODEL=', ''
if (-not $modelPath) { Write-Host "โหลดโมเดลไม่สำเร็จ ดูข้อความด้านบน"; exit 1 }
$lines = Get-Content .env -Encoding UTF8 | Where-Object { $_ -notmatch '^ASR_MODEL=' }
if ($lines -contains "ASR_BEAM_SIZE=5") {  # value written by older installers: slow on a laptop CPU
  Write-Host "ปรับ ASR_BEAM_SIZE จาก 5 เป็น 0 (อัตโนมัติ) ใน .env - บน CPU ถอดเสียงเร็วขึ้นราว 2 เท่า (ต้องการแม่นสุดแก้กลับเป็น 5 ได้)"
  $lines = $lines | ForEach-Object { if ($_ -eq "ASR_BEAM_SIZE=5") { "ASR_BEAM_SIZE=0" } else { $_ } }
}
WriteLF ".env" (@($lines) + "ASR_MODEL=$($modelPath.Trim())")

Say "เปิดระบบ"
Dc up -d
for ($i = 0; $i -lt 90; $i++) {
  try { Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8000/accounts/login/" -TimeoutSec 5 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}

$count = (& docker @Compose exec -T web python manage.py shell -c "from accounts.models import User; print(User.objects.count())") -join ""
if ($count.Trim() -eq "0") {
  Say "สร้างบัญชีพนักงานสอบสวนคนแรก"
  # Read Thai text here (Read-Host gets real Unicode) and pass it as arguments: text typed inside
  # "docker exec" arrives in code page 874, not UTF-8. Only the English-only password is typed there.
  Write-Host "ชื่อ-นามสกุลพิมพ์ภาษาไทยได้ · ชื่อผู้ใช้และรหัสผ่านให้เปลี่ยนแป้นเป็นภาษาอังกฤษ (EN) ก่อนพิมพ์"
  do { $u = (Read-Host "ชื่อผู้ใช้ (ภาษาอังกฤษ เช่น inv1)").Trim() } while ($u -notmatch '^[A-Za-z0-9@.+_-]+$')
  $rank = (Read-Host "ยศ (เช่น ร.ต.อ.)").Trim()
  do { $first = (Read-Host "ชื่อ").Trim() } while (-not $first)
  do { $last = (Read-Host "นามสกุล").Trim() } while (-not $last)
  $position = (Read-Host "ตำแหน่ง (เช่น รอง สว.(สอบสวน))").Trim()
  $officer = @("--username=$u", "--rank=$rank", "--first-name=$first", "--last-name=$last", "--position=$position")
  & docker @Compose exec web python manage.py create_officer @officer
  if ($LASTEXITCODE -ne 0) {
    Write-Host "ยังสร้างบัญชีไม่สำเร็จ - รันคำสั่งนี้ซ้ำได้ ระบบจะถามใหม่ (ข้อมูลอื่นไม่หาย)" -ForegroundColor Yellow
  }
}

Say "เสร็จแล้ว - เปิด http://localhost:8000 ด้วย Chrome หรือ Edge บนเครื่องนี้"
Write-Host "หยุดระบบ:     docker compose -f docker-compose.local.yml stop"
Write-Host "เปิดอีกครั้ง:  docker compose -f docker-compose.local.yml up -d"
