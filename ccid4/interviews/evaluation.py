"""CER (character error rate) of a transcription against a human transcript (SPEC_interview_recorder: สคริปต์ประเมินผล)."""

import re

from rapidfuzz.distance import Levenshtein

from core.thai import to_arabic_digits


def normalize(text: str) -> str:
    return re.sub(r"[\s,.!?\"'“”()\-–]+", "", to_arabic_digits(text or ""))


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)
