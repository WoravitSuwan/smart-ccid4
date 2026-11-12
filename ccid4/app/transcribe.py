"""Speech-to-text on the unit's own machine.

Uses faster-whisper when installed and CCID4_ASR_MODEL is set; nothing is sent
outside. Without it, investigators can paste or type the transcript, or
import a transcript file (JSON / SRT / WebVTT) produced by another on-prem tool.
Speaker labels are assigned by the investigator in the review screen.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config


def asr_available() -> bool:
    if not config.ASR_MODEL:
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def audio_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def to_wav(src: Path) -> Path:
    dst = src.with_suffix(".16k.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
                   check=True, timeout=3600)
    return dst


def transcribe(path: Path) -> list[dict]:
    """Return [{start, end, text}] using the local Whisper model."""
    from faster_whisper import WhisperModel
    model = WhisperModel(config.ASR_MODEL, device=config.ASR_DEVICE, compute_type="auto")
    segments, _ = model.transcribe(str(to_wav(path)), language=config.ASR_LANGUAGE, vad_filter=True)
    return [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segments if s.text.strip()]


_TS = r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:[.,](\d{1,3}))?"


def _secs(a, b, c, ms) -> float:
    if c is None:
        h, mi, sec = 0, int(a), int(b)
    else:
        h, mi, sec = int(a), int(b), int(c)
    return h * 3600 + mi * 60 + sec + (int(ms.ljust(3, "0")) / 1000 if ms else 0)


_ARROW = re.compile(_TS + r"\s*-->\s*" + _TS)
_LINE = re.compile(r"^\s*\[?" + _TS + r"\]?\s+([A-Za-z]+\d*|[ก-๙]+)\s*[:：]?\s+(.+)$")


def parse_transcript(text: str) -> list[dict]:
    """Accepts:
    - JSON list of {start, end, text, speaker?}
    - SRT / WebVTT blocks (speaker as "S1:" or "<v S1>")
    - plain lines: "[00:45] S1: ข้อความ" or "00:45 S1 ข้อความ"
    Returns [{start, end, text, speaker}].
    """
    text = text.strip().lstrip("\ufeff")
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            return [{"start": float(d["start"]), "end": float(d.get("end", d["start"])),
                     "text": str(d["text"]).strip(), "speaker": str(d.get("speaker", "S1"))} for d in data]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    out: list[dict] = []
    if _ARROW.search(text):
        for block in re.split(r"\n\s*\n", text):
            m = _ARROW.search(block)
            if not m:
                continue
            g = m.groups()
            body = block[m.end():].strip()
            spk = "S1"
            sm = re.match(r"^(?:<v\s+([^>]+)>|([A-Za-z]+\d*|[ก-๙]+)\s*:)\s*", body)
            if sm:
                spk = (sm.group(1) or sm.group(2)).strip()
                body = body[sm.end():]
            body = re.sub(r"<[^>]+>", "", body).strip()
            if body:
                out.append({"start": _secs(*g[:4]), "end": _secs(*g[4:]), "text": body, "speaker": spk})
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _LINE.match(line)
        if m:
            g = m.groups()
            start = _secs(*g[:4])
            out.append({"start": start, "end": start, "text": g[5].strip(), "speaker": g[4]})
        elif out:
            out[-1]["text"] += " " + line.strip()
    for i, seg in enumerate(out):
        if seg["end"] <= seg["start"]:
            seg["end"] = out[i + 1]["start"] if i + 1 < len(out) else seg["start"] + 5
    return out
