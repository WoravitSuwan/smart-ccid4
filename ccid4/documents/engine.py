"""Fill .docx templates that contain {placeholder} fields (A6.3).

Word often splits one placeholder over several runs (`{วัน` + `ปัจจุบัน` + `}`), so replacement works on the
whole paragraph (w:p): the runs' text is joined, placeholders are found in the joined text, and the result is
written back into the runs, keeping the formatting of the run where each placeholder starts.

Lists: a table row that contains `{รายการ.ชื่อ}`-style placeholders is repeated once per item of the list
`รายการ` in the values, and each copy is filled from that item.
"""

from __future__ import annotations

import copy
import re
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn

from core.thai import to_thai_digits

PLACEHOLDER = re.compile(r"\{([^{}\n]{1,80})\}")
W_T, W_R, W_P, W_BR, W_TR = qn("w:t"), qn("w:r"), qn("w:p"), qn("w:br"), qn("w:tr")
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def find_placeholders(data: bytes) -> list[str]:
    """Every distinct placeholder name in a template, in order of appearance (used by the template manager)."""
    doc = Document(BytesIO(data))
    seen: dict[str, None] = {}
    for root in _roots(doc):
        for p in root.iter(W_P):
            for m in PLACEHOLDER.finditer(_paragraph_text(p)):
                seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def fill(data: bytes, values: dict, thai_digits: bool = True, allow_empty=(), drop_empty_rows=()) -> tuple[bytes, list[str]]:
    """Return the filled document and the placeholders that had no value (left visible as ……).

    Names in allow_empty print as nothing when empty (e.g. a postcode on an envelope, written in by hand)."""
    doc = Document(BytesIO(data))
    missing: list[str] = []
    for root in _roots(doc):
        _expand_rows(root, values, thai_digits, drop_empty_rows)
        for p in list(root.iter(W_P)):
            if not _split_block(p, values, thai_digits):
                _fill_paragraph(p, values, thai_digits, missing, allow_empty)
    out = BytesIO()
    doc.save(out)
    return out.getvalue(), sorted(set(missing))


# ---------------------------------------------------------------- internals
def _roots(doc):
    yield doc.element.body
    for section in doc.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            if part is not None and part._element is not None:
                yield part._element


def _runs(p):
    """Runs directly in the paragraph or inside hyperlinks/smart tags, but not in nested paragraphs."""
    return [r for r in p.iter(W_R) if _owner(r) is p]


def _owner(el):
    parent = el.getparent()
    while parent is not None and parent.tag != W_P:
        parent = parent.getparent()
    return parent


def _run_text(r) -> str:
    return "".join(t.text or "" for t in r.iter(W_T))


def _paragraph_text(p) -> str:
    return "".join(_run_text(r) for r in _runs(p))


def _set_run_text(r, text: str) -> None:
    """Replace a run's text; '\n' becomes a line break inside the run."""
    for child in list(r):
        if child.tag in (W_T, W_BR):
            r.remove(child)
    for i, line in enumerate(text.split("\n")):
        if i:
            r.append(r.makeelement(W_BR, {}))
        t = r.makeelement(W_T, {})
        t.text = line
        t.set(XML_SPACE, "preserve")
        r.append(t)


def _format(value, thai_digits: bool) -> str:
    if value is None:
        return ""
    text = str(value)
    return to_thai_digits(text) if thai_digits else text


def lookup(values: dict, name: str):
    if name in values:
        return values[name]
    cur = values
    for part in name.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _fill_paragraph(p, values: dict, thai_digits: bool, missing: list[str], allow_empty=()) -> None:
    runs = _runs(p)
    texts = [_run_text(r) for r in runs]
    full = "".join(texts)
    matches = list(PLACEHOLDER.finditer(full))
    if not matches:
        return
    # character offset where each run starts
    starts, pos = [], 0
    for t in texts:
        starts.append(pos)
        pos += len(t)

    lengths = [len(t) for t in texts]

    def run_at(offset):
        for i, (start, n) in enumerate(zip(starts, lengths, strict=True)):
            if start <= offset < start + n:
                return i
        return len(runs) - 1

    for m in reversed(matches):
        name = m.group(1).strip()
        value = lookup(values, name)
        if value in (None, "") and name in allow_empty:
            replacement = ""
        elif value in (None, ""):
            missing.append(name)
            replacement = "……………"
        else:
            replacement = _format(value, thai_digits)
        a, b = m.start(), m.end()
        ia, ib = run_at(a), run_at(b - 1)
        if ia == ib:
            t = texts[ia]
            texts[ia] = t[: a - starts[ia]] + replacement + t[b - starts[ia] :]
        else:
            texts[ia] = texts[ia][: a - starts[ia]] + replacement
            for k in range(ia + 1, ib):
                texts[k] = ""
            texts[ib] = texts[ib][b - starts[ib] :]
    for r, t in zip(runs, texts, strict=True):
        if t != _run_text(r) or "\n" in t:
            _set_run_text(r, t)


def _split_block(p, values: dict, thai_digits: bool) -> bool:
    """A paragraph that is only {name} and whose value has several lines becomes one paragraph per line (same format)."""
    m = PLACEHOLDER.fullmatch(_paragraph_text(p).strip())
    if not m:
        return False
    name = m.group(1).strip()
    value = lookup(values, name)
    if not isinstance(value, str) or "\n" not in value.strip():
        return False
    anchor = p
    for line in (x.strip() for x in value.split("\n")):
        if line:
            clone = copy.deepcopy(p)
            _fill_paragraph(clone, {name: line}, thai_digits, missing=[])
            anchor.addnext(clone)
            anchor = clone
    p.getparent().remove(p)
    return True


def _expand_rows(root, values: dict, thai_digits: bool, drop_empty_rows=()) -> None:
    """Repeat table rows whose placeholders point into a list: {ทรัพย์.ชื่อ} with values['ทรัพย์'] = [{...}, ...]."""
    for tr in list(root.iter(W_TR)):
        text = "".join(_paragraph_text(p) for p in tr.iter(W_P))
        names = [m.group(1).strip() for m in PLACEHOLDER.finditer(text)]
        lists = {n.split(".", 1)[0] for n in names if "." in n and isinstance(values.get(n.split(".", 1)[0]), list)}
        if len(lists) != 1:
            continue
        key = lists.pop()
        items = values[key]
        parent = tr.getparent()
        anchor = tr
        for i, item in enumerate(items, start=1):
            clone = copy.deepcopy(tr)
            row_values = {f"{key}.{k}": v for k, v in item.items()} | {f"{key}.ลำดับ": i}
            for p in clone.iter(W_P):
                _fill_paragraph(p, row_values, thai_digits, missing=[])
            anchor.addnext(clone)
            anchor = clone
        if items or key in drop_empty_rows:
            parent.remove(tr)
            if not items and not any(child.tag == W_TR for child in parent):  # the table is now empty: remove it too
                parent.getparent().remove(parent)
        else:
            for p in tr.iter(W_P):  # keep one empty row so the table still prints
                _fill_paragraph(p, {n: "-" for n in names}, thai_digits, missing=[])
