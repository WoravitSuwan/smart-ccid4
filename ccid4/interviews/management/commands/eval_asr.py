"""ประเมินการถอดเสียง: CER เทียบข้อความที่คนพิมพ์ และเวลาประมวลผลต่อนาทีเสียง เปรียบเทียบหลายโมเดล.

ตัวอย่าง:
  python manage.py eval_asr --audio role_play.webm --reference role_play.txt --model /models/typhoon-large-v3-ct2 --model /models/typhoon-turbo-ct2
ใช้เสียงสวมบทบาทที่เจ้าหน้าที่อัดเท่านั้น ห้ามใช้เสียงผู้ต้องหาจริง
"""

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai import asr
from interviews.evaluation import cer


class Command(BaseCommand):
    help = "วัด CER และเวลาประมวลผลของโมเดลถอดเสียงในเครื่อง"

    def add_arguments(self, parser):
        parser.add_argument("--audio", required=True)
        parser.add_argument("--reference", required=True, help="ไฟล์ข้อความที่คนพิมพ์ (UTF-8)")
        parser.add_argument("--model", action="append", default=[], help="โฟลเดอร์โมเดล CTranslate2 (ระบุได้หลายครั้ง)")

    def handle(self, audio, reference, model, **opts):
        audio, ref = Path(audio), Path(reference).read_text(encoding="utf-8")
        if not audio.exists():
            raise CommandError("ไม่พบไฟล์เสียง")
        minutes = max(asr.duration(audio) / 60, 1e-6)
        models = model or [settings.ASR_MODEL]
        self.stdout.write(f"ไฟล์เสียง {minutes:.1f} นาที")
        for m in models:
            settings.ASR_MODEL, settings.ASR_BACKEND = m, "whisper"
            if not asr.available():
                self.stdout.write(f"{m}: ข้าม (ไม่มี faster-whisper หรือโมเดล)")
                continue
            t0 = time.monotonic()
            segs = asr.transcribe(audio)
            took = time.monotonic() - t0
            hyp = " ".join(s["text"] for s in segs)
            self.stdout.write(f"{m}: CER {cer(ref, hyp):.2%} · {took / minutes:.1f} วินาทีต่อนาทีเสียง")
