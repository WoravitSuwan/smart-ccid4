"""ai.asr — speech to text and speaker separation on the unit's own machine (B2).

transcribe(path) → [{"start","end","text","words":[{"w","s","e","p"}]}]
    whisper: Typhoon Whisper converted to CTranslate2, run with faster-whisper (VAD on, word timestamps, word confidence).
diarize(path) → [{"start","end","speaker"}] or None
    pyannote.audio pipeline loaded from a local folder (DIARIZATION_MODEL); None = the officer labels speakers.
parse_transcript(text) → the same shape, from a transcript made by another on-prem tool (JSON / SRT / WebVTT / plain lines).

Models are downloaded once at install time; HF_HUB_OFFLINE=1 makes sure nothing is fetched while running.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from django.conf import settings

from .llm import AIError

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def available() -> bool:
    if settings.ASR_BACKEND != "whisper" or not settings.ASR_MODEL:
        return False
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def to_flac(src: Path, dst: Path) -> Path:
    """16 kHz mono FLAC for the speech model (the original file is never touched)."""
    subprocess.run([settings.FFMPEG_BIN, "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
                   check=True, timeout=3600)  # fmt: skip
    return dst


def duration(path: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
        return float(out.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


def physical_cores(cpuinfo: str = "/proc/cpuinfo") -> int:
    """แกน CPU จริง (ไม่นับ hyper-threading) ที่โปรแกรมนี้ใช้ได้ — CTranslate2 เร็วสุดเมื่อใช้เท่าจำนวนแกนจริง."""
    usable = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 4)
    cores, phys, core = set(), "0", None
    try:
        with open(cpuinfo, encoding="utf-8", errors="replace") as f:
            for line in [*f, ""]:
                key, _, value = line.partition(":")
                key = key.strip()
                if key == "physical id":
                    phys = value.strip()
                elif key == "core id":
                    core = value.strip()
                elif not line.strip() and core is not None:
                    cores.add((phys, core))
                    phys, core = "0", None
    except OSError:
        pass
    return max(1, min(len(cores) or usable, usable))


def cpu_threads() -> int:
    """ASR_CPU_THREADS=0 → ใช้ทุกแกนจริง (faster-whisper ใช้แค่ 4 เธรดถ้าไม่บอก)."""
    return settings.ASR_CPU_THREADS or physical_cores()


def on_gpu() -> bool:
    if settings.ASR_DEVICE == "cuda":
        return True
    if settings.ASR_DEVICE != "auto":
        return False
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - no CUDA runtime: CPU
        return False


def beam_size() -> int:
    """ASR_BEAM_SIZE=0 → 1 บน CPU (เร็วราว 2 เท่า แม่นลดลงเล็กน้อย) และ 5 บน GPU."""
    return settings.ASR_BEAM_SIZE or (5 if on_gpu() else 1)


def profile() -> str:
    """ข้อความสั้นสำหรับหน้า "ประมวลผล" ว่าเครื่องถอดเสียงตั้งค่าไว้อย่างไร."""
    if on_gpu():
        return f"GPU · beam {beam_size()}"
    return f"CPU {cpu_threads()} แกน · beam {beam_size()}"


def transcribe(path: Path, progress=None) -> list[dict]:
    """progress(seconds_done, seconds_total) is called once the model is ready (0) and then after every section."""
    if not available():
        raise AIError("ยังไม่ได้เปิดใช้การถอดเสียงในเครื่อง (ASR_BACKEND=whisper, ASR_MODEL) · นำเข้าข้อความถอดเสียงแทน")
    from faster_whisper import WhisperModel

    model = WhisperModel(settings.ASR_MODEL, device=settings.ASR_DEVICE, compute_type=settings.ASR_COMPUTE_TYPE,
                         cpu_threads=cpu_threads())  # fmt: skip
    segments, info = model.transcribe(
        str(path),
        language="th",
        beam_size=beam_size(),
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    total = float(getattr(info, "duration", 0) or 0)
    if progress:
        progress(0.0, total)  # model loaded and silence removed: decoding starts now
    out = []
    for s in segments:  # a generator: the model works while we iterate
        if progress:
            progress(float(s.end), total)
        text = s.text.strip()
        if not text:
            continue
        words = [
            {"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 3)}
            for w in (s.words or [])
        ]
        out.append({"start": float(s.start), "end": float(s.end), "text": text, "words": words})
    return out


def diarize(path: Path) -> list[dict] | None:
    if not settings.DIARIZATION_MODEL:
        return None
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        return None
    pipeline = Pipeline.from_pretrained(settings.DIARIZATION_MODEL)
    result = pipeline(str(path))
    return [{"start": float(t.start), "end": float(t.end), "speaker": spk} for t, _, spk in result.itertracks(yield_label=True)]


def assign_speakers(segments: list[dict], turns: list[dict] | None) -> list[dict]:
    """Give each segment the speaker who talks most during it (diarization turns)."""
    for seg in segments:
        if not turns:
            seg.setdefault("speaker", "S1")
            continue
        overlap: dict[str, float] = {}
        for t in turns:
            o = min(seg["end"], t["end"]) - max(seg["start"], t["start"])
            if o > 0:
                overlap[t["speaker"]] = overlap.get(t["speaker"], 0) + o
        seg["speaker"] = max(overlap, key=overlap.get) if overlap else seg.get("speaker", "S1")
    labels = {}
    for seg in segments:  # SPEAKER_00 → S1 in order of first appearance
        labels.setdefault(seg["speaker"], f"S{len(labels) + 1}")
        seg["speaker"] = labels[seg["speaker"]]
    return segments


def repeated(text: str) -> bool:
    """Whisper sometimes loops on silence ("ครับ ครับ ครับ …"): flag unusual repetition."""
    toks = text.split()
    return len(toks) >= 6 and len(set(toks)) <= max(2, len(toks) // 4)


# ---------------------------------------------------------------- import a transcript made elsewhere (on-prem)
_TS = r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:[.,](\d{1,3}))?"
_ARROW = re.compile(_TS + r"\s*-->\s*" + _TS)
# "[00:05] S2: ข้อความ" · "00:05 S2 ข้อความ" · "[00:05] ผู้แจ้ง: ข้อความ" · "[00:05] ข้อความ" (no label: guessed later)
_LINE = re.compile(r"^\s*\[?" + _TS + r"\]?\s+(?:([A-Za-z]+\d*)\s*[:：]?\s+|([ก-๙]{1,20})\s*[:：]\s*)?(.+)$")


def _secs(a, b, c, ms) -> float:
    h, mi, sec = (0, int(a), int(b)) if c is None else (int(a), int(b), int(c))
    return h * 3600 + mi * 60 + sec + (int(ms.ljust(3, "0")) / 1000 if ms else 0)


def parse_transcript(text: str) -> list[dict]:
    text = (text or "").strip().lstrip("﻿")
    if not text:
        return []
    if text[0] == "{" or (text[0] == "[" and not _LINE.match(text.splitlines()[0])):
        try:
            data = json.loads(text)
            items = data["segments"] if isinstance(data, dict) else data
            return [{"start": float(d["start"]), "end": float(d.get("end", d["start"])), "text": str(d["text"]).strip(),
                     "speaker": str(d.get("speaker", "S1")), "words": d.get("words") or []} for d in items]  # fmt: skip
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise AIError("อ่านไฟล์ JSON ไม่ได้ ต้องมี start, end, text") from e
    out: list[dict] = []
    if _ARROW.search(text):
        for block in re.split(r"\n\s*\n", text):
            m = _ARROW.search(block)
            if not m:
                continue
            g = m.groups()
            body, spk = block[m.end() :].strip(), "S1"
            sm = re.match(r"^(?:<v\s+([^>]+)>|([A-Za-z]+\d*|[ก-๙]+)\s*:)\s*", body)
            if sm:
                spk, body = (sm.group(1) or sm.group(2)).strip(), body[sm.end() :]
            body = re.sub(r"<[^>]+>", "", body).strip()
            if body:
                out.append({"start": _secs(*g[:4]), "end": _secs(*g[4:]), "text": body, "speaker": spk, "words": []})
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _LINE.match(line)
        if m:
            g = m.groups()
            start = _secs(*g[:4])
            out.append({"start": start, "end": start, "text": g[6].strip(), "speaker": g[4] or g[5] or "S1", "words": []})
        elif out:
            out[-1]["text"] += " " + line.strip()
    for i, seg in enumerate(out):
        if seg["end"] <= seg["start"]:
            seg["end"] = out[i + 1]["start"] if i + 1 < len(out) else seg["start"] + 5
    return out
