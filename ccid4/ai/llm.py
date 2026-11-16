"""The only way the rest of CCID4 talks to a language model (CLAUDE.md: never call a model from a view).

complete_json(task, context, ...) renders ai/prompts/<task>.txt, asks the configured backend for JSON, checks it
against ai/prompts/<task>.schema.json, retries once, and logs the call (no content) in AICall.

Backends: "fake" (deterministic, for tests and demos without a GPU) and "ollama" (the unit's own server).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from string import Template

from django.conf import settings

from . import schema as jsonschema

PROMPTS = Path(__file__).resolve().parent / "prompts"


class AIError(Exception):
    """Shown to the officer as is (Thai)."""


def prompt_files(task: str) -> tuple[str, dict, str]:
    """Prompt template, schema, and a short version hash of both (+ the shared system prompt)."""
    base = task.split(":")[0]
    text = (PROMPTS / f"{base}.txt").read_text(encoding="utf-8")
    system = (PROMPTS / "_system.txt").read_text(encoding="utf-8")
    sch = json.loads((PROMPTS / f"{base}.schema.json").read_text(encoding="utf-8"))
    version = hashlib.sha256((system + text + json.dumps(sch, sort_keys=True)).encode()).hexdigest()[:10]
    return text, sch, version


def render(task: str, context: dict, extra: dict | None = None) -> tuple[str, str]:
    text, _, _ = prompt_files(task)
    system = (PROMPTS / "_system.txt").read_text(encoding="utf-8")
    body = Template(text).safe_substitute(
        {"context": json.dumps(context, ensure_ascii=False, indent=1, default=str)} | (extra or {})
    )
    return system, body


# ---------------------------------------------------------------- backends
class OllamaLLM:
    name = "ollama"

    def __init__(self):
        if not settings.OLLAMA_MODEL:
            raise AIError("ยังไม่ได้ตั้งค่าโมเดล AI (OLLAMA_MODEL) ติดต่อผู้ดูแลระบบ")
        self.model = settings.OLLAMA_MODEL

    def __call__(self, task: str, context: dict, sch: dict, extra: dict) -> dict:
        system, body = render(task, context, extra)
        payload = {
            "model": self.model,
            "stream": False,
            "format": sch,
            "options": {"temperature": 0.1},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": body}],
        }
        req = urllib.request.Request(
            f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=settings.OLLAMA_TIMEOUT) as resp:  # noqa: S310 - internal URL from settings
                content = json.loads(resp.read())["message"]["content"]
        except (urllib.error.URLError, TimeoutError) as e:
            raise AIError("ติดต่อเครื่อง AI ของหน่วยไม่ได้ ลองใหม่ภายหลังหรือแจ้งผู้ดูแลระบบ") from e
        return json.loads(content)


class FakeLLM:
    """Answers from the case data with simple rules. Never invents facts; good enough to try every screen."""

    name = "fake"
    model = "fake-1"

    def __call__(self, task: str, context: dict, sch: dict, extra: dict) -> dict:
        from . import fake

        return getattr(fake, task.split(":")[0])(context, extra)


def backend():
    return OllamaLLM() if settings.AI_BACKEND == "ollama" else FakeLLM()


# ---------------------------------------------------------------- entry point
def complete_json(task: str, context: dict, *, user=None, case=None, extra: dict | None = None) -> dict:
    from .models import AICall

    _, sch, version = prompt_files(task)
    llm = backend()
    call = AICall(task=task.split(":")[0], backend=llm.name, model=llm.model, prompt_version=version, user=user, case=case)
    t0 = time.monotonic()
    result, problems = None, []
    for attempt in (1, 2):
        call.attempts = attempt
        try:
            result = llm(task, context, sch, extra or {})
        except json.JSONDecodeError:
            problems = ["ไม่ใช่ JSON"]
            continue
        except AIError as e:
            call.error = str(e)[:300]
            break
        problems = jsonschema.errors(result, sch)
        if not problems:
            break
    call.duration_ms = int((time.monotonic() - t0) * 1000)
    call.ok = bool(result is not None and not problems and not call.error)
    if not call.ok and not call.error:
        call.error = ("ผลไม่ตรง schema: " + "; ".join(problems))[:300]
    call.save()
    if not call.ok:
        raise AIError(
            call.error
            if call.error.startswith("ติดต่อ") or call.error.startswith("ยังไม่")
            else "AI ตอบไม่ตรงรูปแบบ 2 ครั้ง ลองใหม่หรือกรอกเอง"
        )
    return result


def clean_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").strip())
