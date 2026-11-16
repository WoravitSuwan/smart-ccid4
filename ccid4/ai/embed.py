"""ai.embed(texts) — vectors for semantic search (A4.7). Fake: character trigram hashing; ollama: e.g. bge-m3."""

import json
import math
import urllib.error
import urllib.request
import zlib

from django.conf import settings

from .llm import AIError


def _fake(text: str, dim: int) -> list[float]:
    v = [0.0] * dim
    s = f"  {''.join((text or '').split())}  "
    for i in range(len(s) - 2):
        v[zlib.crc32(s[i : i + 3].encode()) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _ollama(texts: list[str]) -> list[list[float]]:
    if not settings.OLLAMA_EMBED_MODEL:
        raise AIError("ยังไม่ได้ตั้งค่าโมเดล embedding (OLLAMA_EMBED_MODEL)")
    req = urllib.request.Request(
        f"{settings.OLLAMA_URL.rstrip('/')}/api/embed",
        data=json.dumps({"model": settings.OLLAMA_EMBED_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.OLLAMA_TIMEOUT) as resp:  # noqa: S310 - internal URL
            return json.loads(resp.read())["embeddings"]
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError("ติดต่อเครื่อง AI ของหน่วยไม่ได้") from e


def embed(texts: list[str]) -> list[list[float]]:
    if settings.AI_BACKEND == "ollama":
        return _ollama(texts)
    return [_fake(t, settings.AI_EMBED_DIM) for t in texts]
