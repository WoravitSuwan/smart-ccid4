"""ai.ocr.read(data, content_type) → text. On-premise only.

fake: reads the text layer of PDFs (and plain text); images need a real OCR model.
ollama: an open-weight vision/OCR model served by the unit's Ollama (OCR_MODEL), e.g. a Typhoon OCR model.
"""

import base64
import io
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

from django.conf import settings

from .llm import AIError

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
OCR_PROMPT = "ถอดข้อความภาษาไทยและตัวเลขทั้งหมดในภาพเอกสารนี้ตามที่เห็น รักษาการขึ้นบรรทัด ไม่ต้องสรุปหรืออธิบาย"


# A space wrongly inserted after a Thai above-vowel/tone mark ("บั นทึก") by some PDF text layers.
STRAY_SPACE = re.compile(r"(?<=[\u0e01-\u0e2e]\u0e31) (?=[\u0e01-\u0e2e])")  # ั never ends a syllable
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_thai(text: str) -> str:
    text = CONTROL.sub("", text or "")
    text = STRAY_SPACE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def pdf_text(data: bytes) -> str:
    """Text layer of a PDF. poppler's pdftotext keeps Thai vowels and tone marks; pypdf is the fallback."""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            proc = subprocess.run(["pdftotext", "-enc", "UTF-8", f.name, "-"], capture_output=True, timeout=60, check=False)
        if proc.returncode == 0:
            return clean_thai(proc.stdout.decode("utf-8", errors="replace"))
    from pypdf import PdfReader

    try:
        return clean_thai("\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages))
    except Exception as e:  # noqa: BLE001 - broken/encrypted PDFs
        raise AIError("เปิดไฟล์ PDF ไม่ได้ (ไฟล์เสียหรือมีรหัสผ่าน)") from e


def to_jpeg(data: bytes, content_type: str) -> bytes:
    from PIL import Image

    if content_type in ("image/heic", "image/heif"):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError as e:
            raise AIError("เครื่องนี้ยังอ่านไฟล์ HEIC ไม่ได้ (ติดตั้ง pillow-heif) หรือบันทึกภาพเป็น JPG ก่อน") from e
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((2200, 2200))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def read(data: bytes, content_type: str) -> str:
    if content_type == "text/plain":
        return data.decode("utf-8", errors="replace")
    if content_type == "application/pdf":
        text = pdf_text(data)
        if text:
            return text
        if settings.OCR_BACKEND != "ollama":
            raise AIError("PDF นี้เป็นภาพสแกน ต้องเปิดใช้ OCR ของหน่วย (OCR_BACKEND=ollama) หรืออัปโหลดเป็นภาพ")
        raise AIError("PDF ที่เป็นภาพสแกน ให้บันทึกเป็นภาพ JPG/PNG ทีละหน้าแล้วอัปโหลด")
    if content_type not in IMAGE_TYPES:
        raise AIError("รองรับเฉพาะ JPG PNG HEIC WEBP และ PDF")
    if settings.OCR_BACKEND != "ollama" or not settings.OCR_MODEL:
        raise AIError(
            "ยังไม่ได้เปิดใช้ OCR ภาพของหน่วย (OCR_BACKEND=ollama, OCR_MODEL) · ระหว่างนี้ใช้ PDF ที่มีข้อความ หรือกรอกเอง"
        )
    payload = {
        "model": settings.OCR_MODEL,
        "prompt": OCR_PROMPT,
        "images": [base64.b64encode(to_jpeg(data, content_type)).decode()],
        "stream": False,
    }
    req = urllib.request.Request(
        f"{settings.OLLAMA_URL.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.OLLAMA_TIMEOUT) as resp:  # noqa: S310 - internal URL
            return json.loads(resp.read())["response"].strip()
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError("ติดต่อเครื่อง OCR ของหน่วยไม่ได้") from e
