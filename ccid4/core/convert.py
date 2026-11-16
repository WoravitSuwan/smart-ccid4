"""Office → PDF with LibreOffice headless, on this machine only."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings


def soffice_available() -> bool:
    return shutil.which(settings.SOFFICE_BIN) is not None


def docx_to_pdf(data: bytes, timeout: int = 120) -> bytes:
    """Convert .docx bytes to PDF bytes. Raises RuntimeError when LibreOffice is missing or fails."""
    if not soffice_available():
        raise RuntimeError("ไม่พบ LibreOffice บนเครื่องนี้")
    with tempfile.TemporaryDirectory(prefix="ccid4-") as tmp:
        src = Path(tmp) / "doc.docx"
        src.write_bytes(data)
        profile = Path(tmp) / "profile"
        cmd = [
            settings.SOFFICE_BIN, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
            "--convert-to", "pdf", "--outdir", tmp, str(src),
        ]  # fmt: skip
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        out = Path(tmp) / "doc.pdf"
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError("แปลงเป็น PDF ไม่สำเร็จ")
        return out.read_bytes()
