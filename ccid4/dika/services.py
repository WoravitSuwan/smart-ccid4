"""Hybrid search over Supreme Court rulings: vector (pgvector) + trigram (pg_trgm) + section filter (A4.7)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db import transaction
from pgvector.django import CosineDistance

from ai.embed import embed
from audit.services import log
from core.thai import to_arabic_digits

from .models import DikaRuling, PinnedRuling

SECTION = re.compile(r"(?:ม(?:าตรา)?\.?\s*)(\d+(?:/\d+)?(?:\(\d+\))?)")
VEC_WEIGHT, TRGM_WEIGHT, SECTION_BONUS = 0.65, 0.35, 0.10


def embedding_text(r) -> str:
    return " ".join(x for x in (r.get("sections"), r.get("keywords"), r.get("summary")) if x)


def import_rows(rows: list[dict], source: str, batch: int = 32) -> tuple[int, int]:
    created = updated = 0
    rows = [r for r in rows if r.get("number") and r.get("summary")]
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        vectors = embed([embedding_text(r) for r in chunk])
        with transaction.atomic():
            for r, vec in zip(chunk, vectors, strict=True):
                year = r.get("year")
                _, was_new = DikaRuling.objects.update_or_create(
                    number=r["number"].strip(),
                    defaults={
                        "year": int(year) if str(year or "").isdigit() else None,
                        "sections": r.get("sections", ""), "keywords": r.get("keywords", ""),
                        "summary": r["summary"], "full_text": r.get("full_text", ""), "source": source, "embedding": vec,
                    },
                )  # fmt: skip
                created += was_new
                updated += not was_new
    return created, updated


def sections_in(text: str) -> set[str]:
    return set(SECTION.findall(to_arabic_digits(text or "")))


@dataclass
class Hit:
    ruling: DikaRuling
    score: float
    pinned: bool = False

    @property
    def percent(self) -> int:
        return max(0, min(100, round(self.score * 100)))


def search(query: str, sections: str = "", only_sections: bool = False, limit: int = 10, pinned_ids=()) -> list[Hit]:
    query = (query or "").strip()
    wanted = sections_in(sections) | (sections_in(query) if only_sections else set())
    if not query and not wanted:
        return []
    qs = DikaRuling.objects.exclude(embedding=None)
    if only_sections and wanted:
        from django.db.models import Q

        cond = Q()
        for s in wanted:
            cond |= Q(sections__regex=rf"(^|[^0-9]){re.escape(s)}([^0-9/]|$)")
        qs = qs.filter(cond)
    vec = embed([query or " ".join(wanted)])[0]
    qs = qs.annotate(dist=CosineDistance("embedding", vec), trgm=TrigramWordSimilarity(query or " ", "summary")).order_by("dist")[
        :100
    ]
    hits = []
    for r in qs:
        score = VEC_WEIGHT * (1 - float(r.dist)) + TRGM_WEIGHT * float(r.trgm or 0)
        if wanted and sections_in(r.sections) & wanted:
            score += SECTION_BONUS
        hits.append(Hit(r, min(score, 1.0), r.pk in pinned_ids))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def case_query(case) -> tuple[str, str]:
    return " ".join(x for x in (case.offense, case.title, (case.circumstances or "")[:600]) if x), case.law_sections


def pin(user, case, ruling: DikaRuling, note: str = "") -> PinnedRuling:
    p, created = PinnedRuling.objects.get_or_create(case=case, ruling=ruling, defaults={"pinned_by": user, "note": note})
    if created:
        log(user, "create", "pinned_ruling", p.pk, f"ปักหมุดฎีกา {ruling.number} คดี {case}")
    return p


def unpin(user, case, ruling_id: int) -> None:
    for p in PinnedRuling.objects.filter(case=case, ruling_id=ruling_id):
        log(user, "delete", "pinned_ruling", p.pk, f"เลิกปักหมุดฎีกา {p.ruling.number} คดี {case}")
        p.delete()
