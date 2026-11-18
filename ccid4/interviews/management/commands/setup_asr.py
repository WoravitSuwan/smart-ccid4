"""โหลดโมเดลถอดเสียงมาเก็บในเครื่องครั้งเดียว แล้วทำงานแบบออฟไลน์ (HF_HUB_OFFLINE=1).

    python manage.py setup_asr                         # Typhoon Whisper Turbo: ฝึกด้วยเสียงภาษาไทย ~11,000 ชั่วโมง (MIT) เร็วเท่า turbo
    python manage.py setup_asr --model typhoon-large   # Typhoon Whisper Large v3: แม่นที่สุด ช้ากว่าประมาณ 3–4 เท่าบน CPU
    python manage.py setup_asr --model large-v3-turbo  # Whisper ทั่วไปหลายภาษา (ภาษาไทยหลุดเป็นภาษาอื่นบ่อยกว่า)
    python manage.py setup_asr --model <ชื่อรุ่นบน Hugging Face> --convert
        # โมเดลแบบ transformers (เช่น Typhoon Whisper) → แปลงเป็น CTranslate2; ต้องมี transformers + torch ในเครื่องที่แปลง

นี่เป็นคำสั่งเดียวที่ต่ออินเทอร์เน็ต (ดาวน์โหลดไฟล์โมเดล) ไม่มีการส่งเสียงหรือข้อมูลคดีออกไป
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

ALIASES = {  # MIT licence; Typhoon = official Thai fine-tunes by SCB 10X, converted here from the original weights
    "typhoon-turbo": "typhoon-ai/typhoon-whisper-turbo",
    "typhoon": "typhoon-ai/typhoon-whisper-turbo",
    "typhoon-large": "typhoon-ai/typhoon-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
}
NEEDS_CONVERSION = {"typhoon-ai/typhoon-whisper-turbo", "typhoon-ai/typhoon-whisper-large-v3"}  # transformers weights only
# converting needs torch + transformers once; they go to a temporary folder of this one-off container, not the image
CONVERTER_LIBS = Path(os.environ.get("CCID4_CONVERTER_LIBS") or Path(tempfile.gettempdir()) / "ccid4-convert-libs")
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
TRANSFORMERS = "transformers==5.23.0"  # tested with ctranslate2 4.9.0


# Runs in its own Python (see convert): argv = libs folder ("" = none), repo, output folder, quantization.
CONVERT_SCRIPT = """
import sys
from pathlib import Path
libs, repo, out, quantization = sys.argv[1:5]
if libs:
    sys.path.append(libs)  # after the image's own packages: only what is missing comes from there
from ctranslate2.converters import TransformersConverter
from transformers import AutoFeatureExtractor, AutoTokenizer

# float16 while loading halves the memory needed (a laptop gives Docker ~4 GB); int8 weights load fast on CPU
TransformersConverter(repo, load_as_float16=True, low_cpu_mem_usage=True).convert(out, quantization=quantization, force=True)
# faster-whisper needs the model's own tokenizer.json and preprocessor_config.json (128 mel bins for v3/turbo);
# without them it silently falls back to whisper-tiny's, which shifts language ids and breaks the audio features
folder = Path(out)
if not (folder / "tokenizer.json").exists():
    AutoTokenizer.from_pretrained(repo).backend_tokenizer.save(str(folder / "tokenizer.json"))
if not (folder / "preprocessor_config.json").exists():
    AutoFeatureExtractor.from_pretrained(repo).to_json_file(str(folder / "preprocessor_config.json"))
"""


class Command(BaseCommand):
    help = "ดาวน์โหลดโมเดลถอดเสียงมาไว้ในเครื่อง (ครั้งเดียว) แล้วพิมพ์ค่าที่ต้องตั้งใน .env"

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            default="typhoon-turbo",
            help="typhoon-turbo | typhoon-large | large-v3-turbo | large-v3 | medium | small | ชื่อรุ่นบน Hugging Face",
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
        elif convert or repo in NEEDS_CONVERSION:
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

    def converter_libs(self) -> Path | None:
        """torch + transformers for the one-time conversion (CPU build, ~300 MB) in a temporary folder.

        Returns the folder to add to the converting process' path, or None when this Python already has them.
        """
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401

            return None
        except ImportError:
            pass
        if (CONVERTER_LIBS / "torch").is_dir() and (CONVERTER_LIBS / "transformers").is_dir():
            return CONVERTER_LIBS  # installed by an earlier attempt in this container
        self.stdout.write(
            "ติดตั้งเครื่องมือแปลงโมเดลชั่วคราว (torch + transformers ประมาณ 300 MB ครั้งเดียว ลบทิ้งเองเมื่อเสร็จ)"
        )
        pip = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "-q",
            "--target",
            str(CONVERTER_LIBS),
        ]
        try:
            subprocess.run([*pip, "--index-url", TORCH_CPU_INDEX, "torch"], check=True)
            subprocess.run([*pip, "--upgrade", TRANSFORMERS], check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            raise CommandError(
                "ติดตั้งเครื่องมือแปลงโมเดลไม่สำเร็จ — เครื่องนี้ต้องเข้าถึง download.pytorch.org และ pypi.org ได้ แล้วรันคำสั่งเดิมอีกครั้ง"
            ) from e
        return CONVERTER_LIBS

    def convert(self, repo: str, target: Path, quantization: str) -> None:
        try:
            import ctranslate2  # noqa: F401
        except ImportError as e:
            raise CommandError("ไม่พบ ctranslate2 — สร้าง image ด้วย WITH_ASR=true ก่อน") from e
        libs = self.converter_libs()
        partial = target.with_name(target.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        self.stdout.write(f"กำลังดาวน์โหลดและแปลง {repo} → {target} (ครั้งเดียว ใช้เวลาหลายนาที)")
        # A fresh Python: ctranslate2's converter checks for torch when it is first imported, so torch must be
        # reachable before that import (in this process ctranslate2 may already be loaded without it).
        # It also gives the memory back before the model is loaded for the check.
        proc = subprocess.run([sys.executable, "-c", CONVERT_SCRIPT, str(libs or ""), repo, str(partial), quantization],
                              env={**os.environ, "HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}, check=False)  # fmt: skip
        if proc.returncode != 0 or not (partial / "model.bin").exists():
            shutil.rmtree(partial, ignore_errors=True)
            why = "หน่วยความจำของ Docker ไม่พอ (ปิดโปรแกรมอื่นแล้วลองใหม่)" if proc.returncode in (-9, 137) else "ดูข้อความด้านบน"
            raise CommandError(f"แปลง {repo} ไม่สำเร็จ (รหัส {proc.returncode}) — {why} แล้วรันคำสั่งเดิมอีกครั้ง")
        shutil.rmtree(target, ignore_errors=True)
        partial.rename(target)

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
