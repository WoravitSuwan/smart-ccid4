"""โหลดโมเดลถอดเสียงมาเก็บในเครื่องครั้งเดียว แล้วทำงานแบบออฟไลน์ (HF_HUB_OFFLINE=1).

    python manage.py setup_asr                         # Whisper large-v3-turbo (เร็ว เหมาะกับ CPU)
    python manage.py setup_asr --model large-v3        # แม่นกว่า ช้ากว่าประมาณ 3–4 เท่าบน CPU
    python manage.py setup_asr --model <ชื่อรุ่นบน Hugging Face> --convert
        # โมเดลแบบ transformers (เช่น Typhoon Whisper) → แปลงเป็น CTranslate2; ต้องมี transformers + torch ในเครื่องที่แปลง

นี่เป็นคำสั่งเดียวที่ต่ออินเทอร์เน็ต (ดาวน์โหลดไฟล์โมเดล) ไม่มีการส่งเสียงหรือข้อมูลคดีออกไป
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

ALIASES = {  # pre-converted CTranslate2 builds used by faster-whisper (MIT licence)
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
}


class Command(BaseCommand):
    help = "ดาวน์โหลดโมเดลถอดเสียงมาไว้ในเครื่อง (ครั้งเดียว) แล้วพิมพ์ค่าที่ต้องตั้งใน .env"

    def add_arguments(self, parser):
        parser.add_argument(
            "--model", default="large-v3-turbo", help="large-v3-turbo | large-v3 | medium | small | ชื่อรุ่นบน Hugging Face"
        )
        parser.add_argument("--out", default=os.environ.get("CCID4_MODELS_DIR", "/models"), help="โฟลเดอร์เก็บโมเดล")
        parser.add_argument("--convert", action="store_true", help="แปลงโมเดลแบบ transformers เป็น CTranslate2")
        parser.add_argument("--quantization", default="int8_float16", help="ชนิดตัวเลขตอนแปลง (int8_float16 ใช้ได้ทั้ง CPU/GPU)")

    def handle(self, model, out, convert, quantization, **_):
        os.environ["HF_HUB_OFFLINE"] = "0"  # this command only: fetch the model files
        os.environ["TRANSFORMERS_OFFLINE"] = "0"
        repo = ALIASES.get(model, model)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model if model in ALIASES else repo.split("/")[-1]).strip("-")
        target = Path(out) / "asr" / slug
        target.parent.mkdir(parents=True, exist_ok=True)
        if (target / "model.bin").exists():
            self.stdout.write(f"มีโมเดลอยู่แล้วที่ {target}")
        elif convert:
            self.convert(repo, target, quantization)
        else:
            try:
                from huggingface_hub import snapshot_download
            except ImportError as e:
                raise CommandError("ไม่พบ faster-whisper ในเครื่องนี้ — สร้าง image ด้วย WITH_ASR=true ก่อน") from e
            self.stdout.write(f"กำลังดาวน์โหลด {repo} → {target} (ประมาณ 1–3 GB รอสักครู่)")
            try:
                snapshot_download(
                    repo, local_dir=str(target), allow_patterns=["*.json", "*.bin", "*.txt", "vocabulary.*", "tokenizer*"]
                )
            except Exception as e:  # network, proxy, wrong name: say what to do instead of a traceback
                raise CommandError(
                    f"ดาวน์โหลด {repo} ไม่สำเร็จ ({type(e).__name__}) — ตรวจอินเทอร์เน็ตของเครื่องนี้ "
                    "หรือชื่อรุ่น แล้วรันคำสั่งเดิมอีกครั้ง (ไฟล์ที่โหลดไปแล้วไม่ต้องโหลดใหม่)"
                ) from e
            if not (target / "model.bin").exists():
                shutil.rmtree(target, ignore_errors=True)
                raise CommandError(f"{repo} ไม่ใช่โมเดล CTranslate2 (ไม่มี model.bin) — ลองใหม่โดยเติม --convert")
        self.verify_model(target)
        self.stdout.write(self.style.SUCCESS("พร้อมใช้งาน ตั้งค่าใน .env แล้วเปิดระบบใหม่:"))
        self.stdout.write(f"ASR_BACKEND=whisper\nASR_MODEL={target}")

    def convert(self, repo: str, target: Path, quantization: str) -> None:
        try:
            from ctranslate2.converters import TransformersConverter
        except ImportError as e:
            raise CommandError("ไม่พบ ctranslate2 — สร้าง image ด้วย WITH_ASR=true ก่อน") from e
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as e:
            raise CommandError(
                'การแปลงต้องใช้ transformers และ torch: pip install --user "transformers[torch]" แล้วรันคำสั่งนี้ใหม่'
            ) from e
        self.stdout.write(f"กำลังดาวน์โหลดและแปลง {repo} → {target} (ใช้เวลาหลายนาที)")
        TransformersConverter(repo, copy_files=["tokenizer.json", "preprocessor_config.json"]).convert(
            str(target), quantization=quantization, force=True
        )

    def verify_model(self, target: Path) -> None:
        """Load the model and transcribe 2 seconds of sound, the same way the worker does (catches broken installs)."""
        import subprocess
        import tempfile

        from django.conf import settings
        from faster_whisper import WhisperModel

        from ai import asr

        try:
            model = WhisperModel(str(target), device="cpu", compute_type="int8")
            with tempfile.TemporaryDirectory(prefix="ccid4-asr-check-") as tmp:
                clip = Path(tmp) / "check.flac"
                subprocess.run([settings.FFMPEG_BIN, "-nostdin", "-loglevel", "error", "-f", "lavfi",
                                "-i", "sine=frequency=440:duration=2", "-ac", "1", "-ar", "16000", str(clip)],
                               check=True, timeout=60)  # fmt: skip
                audio = asr.load_audio(clip)
                for vad in (True, False):  # with silence removal (the normal path), then one real decoding pass
                    segments, _ = model.transcribe(audio, language="th", beam_size=1, vad_filter=vad, word_timestamps=True)
                    list(segments)
        except Exception as e:  # noqa: BLE001 - show the reason, not a traceback
            raise CommandError(f"ทดสอบถอดเสียงไม่สำเร็จ ({type(e).__name__}: {e}) — แจ้งผู้ดูแลระบบพร้อมข้อความนี้") from e
        self.stdout.write(f"โหลดโมเดลและทดสอบถอดเสียงสำเร็จ: {target}")
