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

    def upload(self):
        """The active uploaded version (B11), or None when the built-in file is used."""
        from .models import TemplateVersion

        return TemplateVersion.objects.filter(key=self.key, active=True).first()

    def template_bytes(self) -> bytes:
        tv = self.upload()
        if tv:
            with tv.file.open("rb") as f:
                return f.read()
        if not self.path.exists():
            raise FileNotFoundError(f"ยังไม่มีไฟล์เทมเพลตของ {self.title}")
        return self.path.read_bytes()

    def template_version(self) -> str:
        tv = self.upload()
        return tv.label if tv else hashlib.sha256(self.path.read_bytes()).hexdigest()[:12]

    def number_style(self) -> str:
        tv = self.upload()
        return tv.digits if tv else self.digits

    @property
    def per_item(self) -> bool:
        return bool(self.per)


def load() -> tuple[list[str], dict[str, DocType]]:
    """Built-in documents (manifest.yaml) + documents the unit added itself (B11)."""
    groups, docs = load_builtin()
    try:
        from .models import CustomDocType, TemplateVersion

        custom = list(CustomDocType.objects.filter(active=True))
        maps = {t.key: t.mapping for t in TemplateVersion.objects.filter(key__in=[c.key for c in custom], active=True)}
    except Exception:  # noqa: BLE001 - tables not migrated yet (first start)
        return groups, docs
    extra = {}
    for c in custom:
        # readiness of a unit-made document: every system field its template uses must have a value
        reqs = tuple(tuple(sorted({"path": path, "label": f"{name} ({path})", "goto": ""}.items()))
                     for name, path in maps.get(c.key, {}).items() if path and "." not in name)  # fmt: skip
        if c.key not in maps:
            reqs = ((("goto", ""), ("label", "ยังไม่ได้อัปโหลดไฟล์เทมเพลต"), ("path", "_ไม่มีเทมเพลต")),)
        extra[c.key] = DocType(no=100 + c.pk, key=c.key, title=c.title, group=c.group if c.group in groups else groups[-1],
                               per=c.per or None, digits="thai", requires=reqs, new=True)  # fmt: skip
    return groups, docs | extra


@lru_cache(maxsize=1)
def load_builtin() -> tuple[list[str], dict[str, DocType]]:
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
    """Template placeholders whose names differ from the system names (manifest `fields`, or the mapping of an upload)."""
    return apply_aliases_for(doc.upload(), doc, values)


def apply_aliases_for(tv, doc: DocType, values: dict) -> dict:
    pairs = list(doc.fields) + ([(k, v) for k, v in tv.mapping.items() if v] if tv else [])
    if not pairs:
        return values
    out = dict(values)
    for name, path in pairs:
        if "." in name:  # a table-row placeholder {รายการ.ช่อง}: alias the whole list
            src_list, _, src_field = path.partition(".")
            dst_list, _, dst_field = name.partition(".")
            rows = values.get(src_list)
            if isinstance(rows, list):
                merged = (
                    out.get(dst_list) if isinstance(out.get(dst_list), list) and dst_list != src_list else [dict(r) for r in rows]
                )
                for i, r in enumerate(rows):
                    if i < len(merged):
                        merged[i][dst_field] = r.get(src_field, "")
                out[dst_list] = merged
        else:
            out[name] = lookup(values, path)
    return out


def blanks(doc: DocType) -> set[str]:
    """Placeholders the unit chose to leave empty (written in by hand)."""
    tv = doc.upload()
    return {k for k, v in tv.mapping.items() if not v} if tv else set()


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
    if kind == "evidence":
        return reverse("evidence:case", args=[case.pk])
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
