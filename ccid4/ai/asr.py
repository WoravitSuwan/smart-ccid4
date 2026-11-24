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


def has_audio(path: Path) -> bool:
    """True when ffmpeg finds a sound track in the file (any format it can read: phone recordings, video clips…)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=120, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return "audio" in out.stdout


def duration(path: Path) -> float:
    """Seconds of sound in the file. Browser WebM carries no duration header, so it is measured by decoding."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
        return float(out.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run([settings.FFMPEG_BIN, "-nostdin", "-hide_banner", "-i", str(path), "-f", "null", "-"],
                             capture_output=True, text=True, timeout=3600, check=False)  # fmt: skip
        times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr)
        if times:
            h, m, sec = times[-1]
            return int(h) * 3600 + int(m) * 60 + float(sec)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0.0


SAMPLE_RATE = 16000


def load_audio(path: Path):
    """16 kHz mono float32 samples, decoded with ffmpeg.

    faster-whisper would decode with PyAV, whose API changed under it (PyAV 14+: "unexpected keyword argument
    'metadata_errors'"), so the speech model always gets a ready-made array instead.
    """
    import numpy as np

    out = subprocess.run([settings.FFMPEG_BIN, "-nostdin", "-loglevel", "error", "-i", str(path),
                          "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
                         capture_output=True, check=True, timeout=3600)  # fmt: skip
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- keep the text in Thai (and English loanwords)
# Whisper is multilingual and on Thai speech it sometimes drifts into other scripts mid-sentence
# ("ข้าฯ ก็ Ganz ไม่สมัคร", Cyrillic, Arabic, Hebrew...). Tokens that can only belong to another script are suppressed.
_EXTRA_OK = set("\u2018\u2019\u201c\u201d\u2026\u2013\u2014\u00a0")


def foreign_bytes(raw: bytes) -> bool:
    """True when a token can only be (part of) a script other than Thai or ASCII. Never true for a token of Thai text."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        return any(not (ch.isascii() or "\u0e00" <= ch <= "\u0e7f" or ch in _EXTRA_OK) for ch in text)
    for i, b in enumerate(raw):  # a piece of a multi-byte character: Thai letters are E0 B8 xx / E0 B9 xx in UTF-8
        if b == 0xE2 and (i + 1 == len(raw) or raw[i + 1] in (0x80, 0x81)):  # punctuation “ ” … – —
            continue
        if 0xC0 <= b <= 0xDF or b >= 0xE1:  # 2-byte scripts (accented Latin, Cyrillic, Greek, Hebrew, Arabic) or CJK, emoji...
            return True
        if b == 0xE0 and i + 1 < len(raw) and raw[i + 1] not in (0xB8, 0xB9):  # Devanagari, Bengali, Tamil...
            return True
    return False


def _byte_decoder() -> dict[str, int]:
    """GPT-2 byte-level BPE writes each raw byte as a printable character in tokenizer.json; this maps it back."""
    bs = [*range(ord("!"), ord("~") + 1), *range(ord("¡"), ord("¬") + 1), *range(ord("®"), ord("ÿ") + 1)]
    cs, n = bs[:], 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return {chr(c): b for b, c in zip(bs, cs, strict=True)}


_FOREIGN: dict[str, list[int]] = {}


def foreign_token_ids(tokenizer, key: str = "") -> list[int]:
    """Ids of text tokens (below <|endoftext|>) that only another script could use; cached per model."""
    if key in _FOREIGN:
        return _FOREIGN[key]
    decode, ids = _byte_decoder(), []
    for i in range(tokenizer.token_to_id("<|endoftext|>") or 0):
        piece = tokenizer.id_to_token(i)
        try:
            raw = bytes(decode[c] for c in piece or "")
        except KeyError:
            continue
        if raw and foreign_bytes(raw):
            ids.append(i)
    _FOREIGN[key] = ids
    return ids


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
    """ASR_BEAM_SIZE=0 → 5: accuracy first. With turbo models (4 decoder layers) the encoder takes most of the time,
    so a wider beam costs little; set 1 only for the large models on a slow CPU."""
    return settings.ASR_BEAM_SIZE or 5


def model_label() -> str:
    """Model folder name and settings, e.g. "typhoon-turbo · CPU 4 แกน · beam 1"."""
    name = Path(settings.ASR_MODEL).name if settings.ASR_MODEL else "ยังไม่ได้ตั้งโมเดล"
    return f"{name} · {profile()}"


def profile() -> str:
    """ข้อความสั้นสำหรับหน้า "ประมวลผล" ว่าเครื่องถอดเสียงตั้งค่าไว้อย่างไร."""
    if on_gpu():
        return f"GPU · beam {beam_size()}"
    return f"CPU {cpu_threads()} แกน · beam {beam_size()}"


_MODELS: dict[tuple, object] = {}


def get_model():
    """The speech model stays loaded in this worker process: the live transcription calls it every few seconds."""
    from faster_whisper import WhisperModel

    key = (id(WhisperModel), settings.ASR_MODEL, settings.ASR_DEVICE, settings.ASR_COMPUTE_TYPE, cpu_threads())
    if key not in _MODELS:
        _MODELS.clear()  # one model in memory at a time (a laptop gives Docker ~4 GB)
        _MODELS[key] = WhisperModel(settings.ASR_MODEL, device=settings.ASR_DEVICE, compute_type=settings.ASR_COMPUTE_TYPE,
                                    cpu_threads=cpu_threads())  # fmt: skip
    return _MODELS[key]


def transcribe(path: Path, progress=None, start: float = 0.0) -> list[dict]:
    """Transcribe a file (from `start` seconds). progress(seconds_done, seconds_total) is called once the model is
    ready and then after every section."""
    if not available():
        raise AIError("ยังไม่ได้เปิดใช้การถอดเสียงในเครื่อง (ASR_BACKEND=whisper, ASR_MODEL) · นำเข้าข้อความถอดเสียงแทน")
    audio = load_audio(path)
    if start > 0:
        audio = audio[int(start * SAMPLE_RATE) :]
    return transcribe_audio(audio, offset=start, progress=progress)


def transcribe_audio(audio, offset: float = 0.0, progress=None) -> list[dict]:
    """Transcribe 16 kHz mono samples; times in the result are shifted by `offset` seconds."""
    model = get_model()
    suppress = [-1]  # Whisper's default: non-speech symbols
    tokenizer = getattr(model, "hf_tokenizer", None)
    if settings.ASR_THAI_ONLY and tokenizer is not None:
        suppress += foreign_token_ids(tokenizer, settings.ASR_MODEL)
    segments, info = model.transcribe(
        audio,
        language="th",
        suppress_tokens=suppress,
        beam_size=beam_size(),
        # Deterministic decoding only. By default Whisper re-decodes a 30-second window with rising randomness
        # (temperature 0.2…1.0) when its text "compresses too well" (zlib over UTF-8 bytes, threshold 2.4 —
        # tuned for English, which scores ~1.5). Every Thai letter is 3 bytes starting E0 B8/B9, so normal spoken
        # Thai scores 2.3–3.1: the correct text was thrown away and a random re-decoding kept instead
        # ("words right then wrong, the story changes", other languages creeping in).
        temperature=0.0,
        compression_ratio_threshold=None,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    total = offset + float(getattr(info, "duration", 0) or 0)
    if progress:
        progress(offset, total)  # model loaded and silence removed: decoding starts now
    out = []
    for s in segments:  # a generator: the model works while we iterate
        if progress:
            progress(offset + float(s.end), total)
        text = s.text.strip()
        if not text:
            continue
        words = [
            {"w": w.word.strip(), "s": round(offset + w.start, 2), "e": round(offset + w.end, 2), "p": round(w.probability, 3)}
            for w in (s.words or [])
        ]
        out.append({"start": offset + float(s.start), "end": offset + float(s.end), "text": text, "words": words})
    return out


# ---------------------------------------------------------------- live transcription helpers
def decode_from(path: Path, start: float = 0.0):
    """16 kHz mono samples from `start` seconds to the end of a (possibly still growing) recording."""
    import numpy as np

    cmd = [settings.FFMPEG_BIN, "-nostdin", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]  # seek before reading: earlier audio is not decoded again
    cmd += ["-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600, check=False)  # an unfinished last cluster is normal
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0


LIVE_MIN, LIVE_TARGET, LIVE_MAX = 8.0, 12.0, 28.0  # seconds per window: enough context, under Whisper's 30 s
PAUSE = 0.5  # a breath between phrases
GUARD = 1.0  # do not cut this close to the newest audio: the word may continue in the next upload
FRAME = 0.03


def pause_cuts(audio, final: bool = False) -> list[int]:
    """Sample positions where the not-yet-transcribed audio is cut into windows: in pauses, 8–28 s apart.

    Not final: only windows that are safely behind the newest audio. Final: everything, the rest in a last window.
    """
    import numpy as np

    sr, frame = SAMPLE_RATE, int(FRAME * SAMPLE_RATE)
    end = len(audio)
    if end == 0:
        return []
    n = end // frame
    if n:
        frames = audio[: n * frame].reshape(n, frame)
        db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
        silent = db < max(-50.0, float(np.percentile(db, 90)) - 30.0)
    else:
        db, silent = np.zeros(0), np.zeros(0, dtype=bool)
    pauses, trailing, run = [], None, 0  # cut positions inside pauses
    for i, quiet in enumerate([*silent, False]):
        if quiet:
            run += 1
            continue
        if run * FRAME >= PAUSE:
            first = i - run
            if i < n:
                pauses.append(((first + i) // 2) * frame)
            else:  # the speaker is silent right now: safe to cut just after the silence began
                trailing = first * frame + int(PAUSE / 2 * sr)
        run = 0
    cuts, pos = [], 0
    limit = end if final else end - int(GUARD * sr)
    safe = [p for p in pauses if p <= limit] + ([trailing] if trailing is not None else [])
    while pos < end:
        if final and end - pos <= LIVE_MAX * sr:
            cuts.append(end)
            break
        lo, hi = pos + int(LIVE_MIN * sr), pos + int(LIVE_MAX * sr)
        options = [p for p in safe if lo <= p <= hi]
        if options:
            later = [p for p in options if p >= pos + int(LIVE_TARGET * sr)]
            cut = later[0] if later else options[-1]
        elif hi <= limit:  # 28 s without a pause: cut at the quietest moment
            a, b = lo // frame, hi // frame
            cut = (a + int(np.argmin(db[a:b]))) * frame if b > a else hi
        else:
            break
        cuts.append(cut)
        pos = cut
    return cuts


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
