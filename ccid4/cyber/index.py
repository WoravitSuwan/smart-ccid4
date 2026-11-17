"""ดัชนีตัวระบุข้ามคดี (B4): สร้างใหม่เมื่อข้อมูลคดีเปลี่ยน · หาคดีอื่นที่มีตัวระบุเดียวกัน · แจ้งเตือนเจ้าของคดีทั้งสองฝ่าย."""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction
from django.urls import reverse

from core.notify import notify

from . import identifiers
from .models import Identifier


def collect(case) -> list[tuple[str, str, int | None, str]]:
    """(kind, raw value, party id, where it is in the case) for everything that can link cases."""
    out = []
    for cp in case.parties.select_related("person"):
        who = f"{cp.get_role_display()} {cp.person.full_name}"
        if cp.person.phone:
            out.append(("phone", cp.person.phone, cp.pk, f"เบอร์โทร{who}"))
        if cp.person.citizen_id:
            out.append(("citizen_id", cp.person.citizen_id, cp.pk, f"เลขบัตร{who}"))
    for a in case.bank_accounts.all():
        out.append(("bank_account", a.account_no, a.party_id, "บัญชีผู้เสียหาย" if a.is_victim else f"บัญชีปลายทาง {a.bank}".strip()))
    for o in case.online_accounts.all():
        out.append((o.identifier_kind, o.value, o.party_id, o.get_kind_display()))
        if o.kind == "url":
            out.append(("domain", o.value, o.party_id, "โดเมนเว็บไซต์"))
    for w in case.crypto_wallets.all():
        out.append(("crypto", w.address, w.party_id, f"กระเป๋า {w.get_network_display()}"))
    for t in case.crypto_txs.all():
        for addr in (t.from_address, t.to_address):
            if addr:
                out.append(("crypto", addr, None, "ที่อยู่ในธุรกรรมคริปโต"))
    return out


def reindex_case(case) -> int:
    """Rebuild this case's index; returns how many identifiers are newly shared with other cases."""
    entries: dict[tuple[str, str], tuple[str, int | None, str]] = {}
    for kind, value, party_id, source in collect(case):
        e = identifiers.entry(kind, value)
        if e:
            entries.setdefault((kind, e[0]), (e[1], party_id, source))
    with transaction.atomic():
        old = set(Identifier.objects.filter(case=case).values_list("kind", "digest"))
        Identifier.objects.filter(case=case).delete()
        Identifier.objects.bulk_create(
            [
                Identifier(case=case, kind=k, digest=d, masked=m, party_id=p, source=s[:120])
                for (k, d), (m, p, s) in entries.items()
            ]
        )
    if case.deleted_at:
        return 0
    return announce(case, set(entries) - old, entries)


def announce(case, keys, entries) -> int:
    """Notify both owners once, when an identifier first appears in this case and already exists elsewhere."""
    if not keys:
        return 0
    others = (
        Identifier.objects.filter(digest__in=[d for _, d in keys], case__deleted_at__isnull=True)
        .exclude(case=case)
        .select_related("case__owner")
    )
    shared = 0
    for (kind, digest), rows in _group(others).items():
        if (kind, digest) not in keys:
            continue
        shared += 1
        masked = entries[(kind, digest)][0]
        label = identifiers.KINDS[kind]
        cases = list({r.case_id: r.case for r in rows}.values())
        notify(
            [case.owner],
            "identifier_match",
            f"พบ{label} {masked} ในคดีอื่น {len(cases)} คดี",
            " · ".join(f"{c.label} ({c.owner.display_name})" for c in cases[:5]),
            f"{reverse('cyber:network')}?case={case.pk}",
        )
        for other in cases:
            notify(
                [other.owner],
                "identifier_match",
                f"คดี {other.label}: {label} {masked} พบในคดีใหม่",
                f"คดี {case.label} ของ {case.owner.display_name}",
                f"{reverse('cyber:network')}?case={other.pk}",
            )
    return shared


def _group(rows) -> dict[tuple[str, str], list[Identifier]]:
    out = defaultdict(list)
    for r in rows:
        out[(r.kind, r.digest)].append(r)
    return out


def links(case) -> dict[tuple[str, str], list]:
    """{(kind, digest): [other Case, …]} for every identifier of this case that also appears in other live cases."""
    mine = list(Identifier.objects.filter(case=case).values_list("kind", "digest"))
    if not mine:
        return {}
    keys = set(mine)
    rows = (
        Identifier.objects.filter(digest__in=[d for _, d in mine], case__deleted_at__isnull=True)
        .exclude(case=case)
        .select_related("case__owner")
    )
    out: dict[tuple[str, str], dict] = defaultdict(dict)
    for r in rows:
        if (r.kind, r.digest) in keys:
            out[(r.kind, r.digest)][r.case_id] = r.case
    return {k: list(v.values()) for k, v in out.items()}


def others_for(link_map, kind: str, value: str) -> list:
    e = identifiers.entry(kind, value)
    return link_map.get((kind, e[0]), []) if e else []


def linked_case_count(link_map) -> int:
    return len({c.pk for cases in link_map.values() for c in cases})


# ---------------------------------------------------------------- network page (cases ↔ identifiers)
def network(user, kind: str = "", date_from=None, date_to=None, focus=None) -> dict:
    """Graph of identifiers found in 2+ cases. Other cases show their number and owner only (B4 สิทธิ์การดู)."""
    from django.db.models import Count

    from cases.models import Case

    qs = Identifier.objects.filter(case__deleted_at__isnull=True)
    if kind:
        qs = qs.filter(kind=kind)
    if date_from:
        qs = qs.filter(case__reported_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(case__reported_at__date__lte=date_to)
    shared = qs.values("kind", "digest").annotate(n=Count("case", distinct=True)).filter(n__gte=2)
    keys = {(g["kind"], g["digest"]) for g in shared}
    rows = [r for r in qs.filter(digest__in=[d for _, d in keys]).select_related("case__owner") if (r.kind, r.digest) in keys]
    visible = set(Case.objects.visible_to(user).values_list("pk", flat=True))
    groups = _group(rows)
    keep = {}
    for key, items in groups.items():
        case_ids = {r.case_id for r in items}
        if not case_ids & visible:
            continue  # nothing of this link is the user's to see
        if focus and focus not in case_ids:
            continue
        keep[key] = items
    nodes, edges, seen = [], [], set()
    for (k, d), items in keep.items():
        nid = f"i-{k}-{d[:16]}"
        nodes.append({"data": {"id": nid, "type": "identifier", "kind": k, "kind_label": identifiers.KINDS[k],
                               "label": items[0].masked, "cases": len({r.case_id for r in items})}})  # fmt: skip
        for r in items:
            cid = f"c-{r.case_id}"
            can = r.case_id in visible
            if cid not in seen:
                seen.add(cid)
                c = r.case
                nodes.append({"data": {"id": cid, "type": "case", "label": c.label, "owner": c.owner.display_name, "visible": can,
                                       "focus": c.pk == focus, "url": reverse("cases:detail", args=[c.pk]) if can else "",
                                       "money_url": reverse("cyber:case", args=[c.pk]) if can else ""}})  # fmt: skip
            edges.append({"data": {"id": f"e-{r.pk}", "source": cid, "target": nid, "label": r.source if can else ""}})
    return {"nodes": nodes, "edges": edges, "identifiers": len(keep), "cases": len(seen)}


def network_rows(graph: dict) -> list[list]:
    """Rows for the Excel export: one per identifier × case."""
    by_id = {n["data"]["id"]: n["data"] for n in graph["nodes"]}
    rows = []
    for e in graph["edges"]:
        c, i = by_id[e["data"]["source"]], by_id[e["data"]["target"]]
        rows.append(
            [i["kind_label"], i["label"], i["cases"], c["label"], c["owner"], e["data"]["label"] or "— (ไม่มีสิทธิ์ดูรายละเอียด)"]
        )
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    return rows
