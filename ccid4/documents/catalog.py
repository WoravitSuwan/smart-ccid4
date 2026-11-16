"""The list of documents (manifest.yaml) and whether each one can be issued for a case (A6.1)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from django.urls import reverse

from .engine import lookup

LIBRARY = Path(__file__).resolve().parent / "library"
PER_ROLES = {"accuser": ["accuser"], "suspect": ["suspect"], "witness": ["witness"], "people": ["accuser", "witness"]}


@dataclass(frozen=True)
class DocType:
    no: int
    key: str
    title: str
    group: str
    per: str | None
    digits: str
    requires: tuple
    fields: tuple = ()
    new: bool = False

    @property
    def path(self) -> Path:
        return LIBRARY / f"{self.key}.docx"

    def template_bytes(self) -> bytes:
        return self.path.read_bytes()

    def template_version(self) -> str:
        return hashlib.sha256(self.template_bytes()).hexdigest()[:12]

    @property
    def per_item(self) -> bool:
        return bool(self.per)


@lru_cache(maxsize=1)
def load() -> tuple[list[str], dict[str, DocType]]:
    data = yaml.safe_load((LIBRARY / "manifest.yaml").read_text(encoding="utf-8"))
    docs = {}
    for d in data["documents"]:
        docs[d["key"]] = DocType(
            no=d["num"], key=d["key"], title=d["title"], group=d["group"], per=d.get("per"), digits=d.get("digits", "thai"),
            requires=tuple(tuple(sorted(r.items())) for r in d.get("requires", [])),
            fields=tuple(sorted((d.get("fields") or {}).items())), new=bool(d.get("new")),
        )  # fmt: skip
    return data["groups"], dict(sorted(docs.items(), key=lambda kv: kv[1].no))


def get(key: str) -> DocType:
    return load()[1][key]


def apply_aliases(doc: DocType, values: dict) -> dict:
    """Template placeholders whose names differ from the system names (manifest `fields`)."""
    if not doc.fields:
        return values
    return values | {name: lookup(values, path) for name, path in doc.fields}


# ---------------------------------------------------------------- readiness
@dataclass
class Missing:
    label: str
    url: str


@dataclass
class ItemState:
    """One person / letter a per-item document can be issued for."""

    id: int
    label: str
    missing: list[Missing] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing


@dataclass
class DocState:
    doc: DocType
    missing: list[Missing]  # case-level
    items: list[ItemState]

    @property
    def ready(self) -> bool:
        if self.missing:
            return False
        return not self.doc.per or any(i.ready for i in self.items)

    @property
    def all_missing(self) -> list[Missing]:
        """What to show on hover: case-level gaps, then the first item's gaps (or 'add a person')."""
        if self.missing or not self.doc.per:
            return self.missing
        if not self.items:
            return [Missing(f"ยังไม่มี{ITEM_NOUN[self.doc.per]}ในคดี", "")]
        if not any(i.ready for i in self.items):
            return self.items[0].missing
        return []


ITEM_NOUN = {"accuser": "ผู้กล่าวหา", "suspect": "ผู้ต้องหา", "witness": "พยาน", "people": "ผู้กล่าวหาหรือพยาน", "letter": "หนังสือออกภายนอก"}
TAB_OF_ROLE = {"accuser": "accuser", "suspect": "suspects", "witness": "witnesses"}


def goto_url(case, goto: str, item=None) -> str:
    if not goto:
        return ""
    kind, _, rest = goto.partition(":")
    if kind == "profile":
        return f"{reverse('accounts:profile')}#{rest}"
    base = reverse("cases:detail", args=[case.pk])
    if kind == "tab":
        tab, _, field_name = rest.partition(":")
        return f"{base}?focus={field_name}#{tab}" if field_name else f"{base}#{tab}"
    if kind == "person" and item is not None:
        return f"{base}#{TAB_OF_ROLE.get(item.role, 'info')}"
    return base


def is_empty(value) -> bool:
    return value in (None, "", [], {}) or (isinstance(value, str) and not value.strip())


def state_for(case, doc: DocType, values: dict, item_values: dict) -> DocState:
    """`values` = context without an item; `item_values` = {item_id: (label, item obj, values-with-item)}."""
    case_reqs = [dict(r) for r in doc.requires if not dict(r)["path"].startswith(("บุคคล.", "หนังสือ."))]
    item_reqs = [dict(r) for r in doc.requires if dict(r)["path"].startswith(("บุคคล.", "หนังสือ."))]
    missing = [Missing(r["label"], goto_url(case, r.get("goto", ""))) for r in case_reqs if is_empty(lookup(values, r["path"]))]
    items = []
    for item_id, (label, obj, vals) in item_values.items():
        gaps = [
            Missing(r["label"], goto_url(case, r.get("goto", ""), obj)) for r in item_reqs if is_empty(lookup(vals, r["path"]))
        ]
        items.append(ItemState(item_id, label, gaps))
    return DocState(doc, missing, items)
