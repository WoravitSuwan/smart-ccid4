from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from audit.services import log
from parties.forms import PartyForm, PartyFormSet, PropertyFormSet
from parties.models import CaseParty, PropertyItem
from parties.services import party_initial

from . import services
from .forms import CaseCreateForm, CaseDetailForm
from .models import Case, CaseStatus, CaseType

TABS = [
    ("info", "ข้อมูลคดี"),
    ("accuser", "ผู้กล่าวหา"),
    ("suspects", "ผู้ต้องหา"),
    ("witnesses", "พยาน"),
    ("investigator", "พนักงานสอบสวน"),
    ("dika", "ค้นฎีกา"),
]
SOON_TABS: dict[str, str] = {}


def visible(user):
    return Case.objects.visible_to(user)


def get_case(user, pk):
    return get_object_or_404(visible(user), pk=pk)


@login_required
def case_list(request):
    base = visible(request.user)
    q, status, ctype = request.GET.get("q", "").strip(), request.GET.get("status", ""), request.GET.get("type", "")
    qs = services.filter_cases(
        base.select_related("owner").prefetch_related("parties__person", "parties__detentions", "tasks"), q, status, ctype
    )
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "counts": services.summary_counts(base),
        "q": q,
        "status": status,
        "ctype": ctype,
        "types": CaseType.choices,
        "statuses": CaseStatus.choices,
        "active": "cases",
    }
    if request.headers.get("HX-Request"):
        return render(request, "cases/_table.html", ctx)
    return render(request, "cases/list.html", ctx)


@login_required
def case_create(request):
    if not request.user.can("case.edit"):
        raise PermissionDenied
    form = CaseCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        case = services.create_case(request.user, **form.cleaned_data)
        messages.success(request, f"สร้างคดี {case} แล้ว")
        return redirect("cases:detail", pk=case.pk)
    return render(request, "cases/create.html", {"form": form, "active": "cases"})


@login_required
@require_POST
def case_delete(request, pk):
    case = get_case(request.user, pk)
    if not (request.user.can("case.edit") and (case.owner_id == request.user.pk or request.user.can("case.view_group"))):
        raise PermissionDenied
    reason = request.POST.get("reason", "").strip()
    if request.POST.get("confirm") != case.label or not reason:
        messages.error(request, "พิมพ์เลขคดีให้ตรงและระบุเหตุผลก่อนลบ")
        return redirect("cases:list")
    services.soft_delete(request.user, case, reason)
    messages.success(request, f"ลบคดี {case} แล้ว (ผู้ดูแลระบบกู้คืนได้)")
    return redirect("cases:list")


def can_edit(user, case: Case) -> bool:
    if not user.can("case.edit"):
        return False
    if case.owner_id == user.pk or case.shared_with.filter(pk=user.pk).exists():
        return True
    return user.can("case.view_group") and case.owner.unit_group == user.unit_group


def build_forms(case: Case, data=None):
    parties = list(case.parties.select_related("person").prefetch_related("statements__created_by"))
    real_ids = {cp.pk: cp.person.citizen_id for cp in parties}
    by_role = {r: [cp for cp in parties if cp.role == r] for r in ("accuser", "suspect", "witness")}
    acc = by_role["accuser"][0] if by_role["accuser"] else None
    forms = {
        "case_form": CaseDetailForm(data, instance=case, prefix="c"),
        "accuser_form": PartyForm(data, prefix="acc", initial=party_initial(acc) if acc else {}, real_citizen_ids=real_ids),
        "property_fs": PropertyFormSet(data, prefix="prop", queryset=PropertyItem.objects.filter(case=case)),
        "suspect_fs": PartyFormSet(
            data, prefix="sus", initial=[party_initial(cp) for cp in by_role["suspect"]], real_citizen_ids=real_ids
        ),
        "witness_fs": PartyFormSet(
            data, prefix="wit", initial=[party_initial(cp) for cp in by_role["witness"]], real_citizen_ids=real_ids
        ),
    }
    # Attach the stored person to each bound form so templates can show statement versions and reveal buttons.
    forms["accuser_form"].party = acc
    for key, role in (("suspect_fs", "suspect"), ("witness_fs", "witness")):
        for i, f in enumerate(forms[key].forms):
            f.party = by_role[role][i] if i < len(by_role[role]) else None
    from deadlines.services import custody_for, load_rules

    rs = load_rules()
    for f in forms["suspect_fs"].forms:
        f.custody = custody_for(f.party, case, rs) if f.party else None
    return forms


def make_read_only(forms) -> None:
    for obj in forms.values():
        for form in getattr(obj, "forms", [obj]):
            for field in form.fields.values():
                field.disabled = True


def tab_errors(forms) -> dict:
    return {
        "info": bool(forms["case_form"].errors),
        "accuser": bool(forms["accuser_form"].errors) or any(f.errors for f in forms["property_fs"].forms),
        "suspects": any(f.errors for f in forms["suspect_fs"].forms) or bool(forms["suspect_fs"].non_form_errors()),
        "witnesses": any(f.errors for f in forms["witness_fs"].forms) or bool(forms["witness_fs"].non_form_errors()),
    }


@login_required
def case_detail(request, pk):
    base = get_case(request.user, pk)
    if request.method == "GET" and not base.tasks.exclude(rule_key="").exists():
        from deadlines.services import sync_rule_tasks

        sync_rule_tasks(base)  # first open of a case made before phase 3 (or imported)
    case = get_object_or_404(
        visible(request.user).select_related("owner").prefetch_related("parties__person", "parties__detentions", "tasks"), pk=pk
    )
    editable = can_edit(request.user, case)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        forms = build_forms(case, request.POST)
        if all(f.is_valid() for f in forms.values()):
            circ_ai = request.POST.get("circ_ai", "").strip()
            from_ai = bool(circ_ai) and circ_ai == (forms["case_form"].cleaned_data.get("circumstances") or "").strip()
            changed = services.save_case(request.user, case, **forms, circumstances_source="ai" if from_ai else "manual")
            if changed:
                messages.success(request, "บันทึกแล้ว: " + " · ".join(changed))
            else:
                messages.info(request, "ไม่มีข้อมูลที่เปลี่ยน")
            tab = request.POST.get("tab", "info")
            return redirect(f"{case.get_absolute_url()}#{tab if tab in dict(TABS) or tab in ('money', 'evidence') else 'info'}")
        messages.error(request, "ยังบันทึกไม่ได้ ตรวจช่องที่มีกรอบสีแดงในแท็บที่มีจุดเตือน")
        status = 400
    else:
        forms = build_forms(case)
        if not editable:
            make_read_only(forms)
        log(request.user, "view", "case", case.pk, f"เปิดหน้าคดี {case}")
        status = 200
    errors = tab_errors(forms) if request.method == "POST" else {}
    from deadlines.forms import DetentionForm, TaskForm
    from deadlines.services import panel_context, suggested_start

    from .letters import LETTER_TEMPLATES

    detention_forms = []
    for f in forms["suspect_fs"].forms:
        c = getattr(f, "custody", None)
        if c and c.plan and not (c.plan.used_times and c.plan.exhausted):
            limit = c.plan.per_days if not c.plan.used_times else min(c.plan.per_days, c.plan.remaining_days)
            df = DetentionForm(prefix=f"det{f.party.pk}", initial={"start_date": suggested_start(c), "days": limit})
            df.fields["days"].max_value = limit
            df.fields["days"].widget.attrs["max"] = limit
            detention_forms.append((f.party, c, df))
    from cyber import index as cyber_index
    from cyber import money as cyber_money

    links = cyber_index.links(case)
    party_forms = [forms["accuser_form"], *forms["suspect_fs"].forms, *forms["witness_fs"].forms]
    for f in party_forms:  # B4: "พบในคดีอื่น n คดี" next to the person's phone / citizen ID
        cp = getattr(f, "party", None)
        f.links = []
        if cp:
            for kind, label, value in (
                ("phone", "เบอร์โทร", cp.person.phone),
                ("citizen_id", "เลขบัตรประชาชน", cp.person.citizen_id),
            ):
                others = cyber_index.others_for(links, kind, value)
                if others:
                    f.links.append({"label": label, "cases": others})
    tabs = TABS + ([("money", "เส้นทางเงิน")] if case.case_type == "online" else []) + [("evidence", "พยานหลักฐาน/ของกลาง")]
    evidence_summary = {"files": case.evidence_files.filter(withdrawn_at__isnull=True).count(), "exhibits": case.exhibits.count(),
                        "events": case.custody_events.count()}  # fmt: skip
    money_summary = None
    if case.case_type == "online":
        rows = cyber_money.account_rows(case, links)
        money_summary = {"total": cyber_money.total_loss(case), "accounts": sum(1 for r in rows if not r.account.is_victim),
                         "transfers": case.transfers.count(), "linked_accounts": sum(1 for r in rows if r.other_cases),
                         "online": case.online_accounts.count(), "wallets": case.crypto_wallets.count()}  # fmt: skip
    ctx = (
        forms
        | panel_context(case)
        | {
            "all_tasks": list(case.tasks.select_related("done_by", "created_by").order_by("-created_at")),
            "letters": case.letters.all(),
            "letter_templates": LETTER_TEMPLATES,
            "task_form": TaskForm(),
            "detention_forms": detention_forms,
            "case": case,
            "editable": editable,
            "tabs": tabs,
            "soon_tabs": SOON_TABS,
            "linked_count": cyber_index.linked_case_count(links),
            "money": money_summary,
            "evidence_summary": evidence_summary,
            "errors": errors,
            "first_error_tab": next((t for t, e in errors.items() if e), ""),
            "can_reveal": request.user.can("pii.reveal"),
            "can_confirm": request.user.can("statement.confirm"),
            "active": "cases",
        }
    )
    return render(request, "cases/detail.html", ctx, status=status)


@login_required
@require_POST
def reveal_citizen_id(request, pk, party_id):
    """Show a full citizen ID for one person. Needs pii.reveal and is always written to the audit log."""
    case = get_case(request.user, pk)
    if not request.user.can("pii.reveal"):
        raise PermissionDenied
    cp = get_object_or_404(CaseParty.objects.select_related("person"), pk=party_id, case=case)
    log(request.user, "reveal", "person", cp.person_id, f"ดูเลขประจำตัวประชาชนเต็มของ{cp.get_role_display()} {cp.person} คดี {case}")
    name = request.POST.get("field", "")
    if not name.endswith("-citizen_id") or not name.replace("-", "").replace("_", "").isalnum():
        name = "citizen_id"
    return HttpResponse(
        format_html(
            '<input type="text" name="{}" value="{}" class="input" id="id_{}" maxlength="20" inputmode="numeric" '
            'aria-describedby="{}-hint"><p class="text-xs text-warning-ink" id="{}-hint">แสดงเต็มแล้ว (บันทึกในประวัติการใช้งาน)</p>',
            name,
            cp.person.citizen_id,
            name,
            name,
            name,
        )
    )


@login_required
def case_timeline(request, pk):
    """B10: chronological view, exportable as PDF (or .docx when LibreOffice is not installed)."""
    from django.http import HttpResponse as Resp

    from core.convert import docx_to_pdf

    from .timeline import KIND_LABELS, events, to_docx

    case = get_case(request.user, pk)
    items = events(case)
    fmt = request.GET.get("format")
    if fmt in ("pdf", "docx"):
        data = to_docx(case, items)
        name = f"timeline-{case.pk}"
        if fmt == "pdf":
            try:
                data, ctype, name = docx_to_pdf(data), "application/pdf", f"{name}.pdf"
            except RuntimeError as e:
                messages.error(request, f"{e} · ดาวน์โหลดเป็น Word แทน")
                return redirect(f"{request.path}?format=docx")
        else:
            ctype, name = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"{name}.docx"
        log(request.user, "export", "case", case.pk, f"ส่งออก Timeline คดี {case} ({fmt})")
        resp = Resp(data, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        return resp
    log(request.user, "view", "case", case.pk, f"เปิด Timeline คดี {case}")
    return render(request, "cases/timeline.html", {"case": case, "items": items, "kinds": KIND_LABELS, "active": "cases"})
