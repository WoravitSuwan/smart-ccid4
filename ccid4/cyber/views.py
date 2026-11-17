import json
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from ai.llm import AIError
from audit.services import log
from cases.models import Case
from cases.views import can_edit, get_case

from . import crypto, identifiers, index, money, services
from .forms import BANKS, AccountForm, OnlineForm, TransferForm, TxForm, WalletForm
from .models import BankAccount, CryptoTx, CryptoWallet, MergeProposal, MoneyTransfer, OnlineAccount

CHANNELS = ["โมบายแบงก์กิ้ง", "อินเทอร์เน็ตแบงก์กิ้ง", "พร้อมเพย์", "ตู้ ATM", "เคาน์เตอร์ธนาคาร", "บัตรเครดิต/เดบิต", "วอลเล็ต (ทรูมันนี่ ฯลฯ)"]
TMP = "cyber/tmp"


def case_for(request, pk, edit=False) -> Case:
    case = get_case(request.user, pk)
    if edit and not can_edit(request.user, case):
        raise PermissionDenied
    return case


def back(case, tab):
    return redirect(f"{reverse('cyber:case', args=[case.pk])}#{tab}")


def form_errors(form) -> str:
    return " ".join(f"{form.fields[k].label if k in form.fields else ''} {' '.join(v)}".strip() for k, v in form.errors.items())


# ---------------------------------------------------------------- the money-flow page of one case
@login_required
def case_page(request, pk):
    case = case_for(request, pk)
    links = index.links(case)
    rows = money.account_rows(case, links)
    online = list(case.online_accounts.select_related("party__person"))
    for o in online:
        o.other_cases = index.others_for(links, o.identifier_kind, o.value)
    wallets = list(case.crypto_wallets.select_related("party__person"))
    for w in wallets:
        w.valid, w.message = crypto.validate(w.network, w.address)
        w.other_cases = index.others_for(links, "crypto", w.address)
    mules = [r for r in rows if not r.account.is_victim]
    log(request.user, "view", "money_flow", case.pk, f"เปิดหน้าเส้นทางเงิน คดี {case}")
    ctx = {
        "case": case, "editable": can_edit(request.user, case), "rows": rows, "mules": mules,
        "transfers": case.transfers.select_related("from_account", "to_account"), "total_loss": money.total_loss(case),
        "status_counts": {s.label: sum(1 for r in mules if r.account.status == s.value) for s in BankAccount.Status},
        "linked": index.linked_case_count(links), "online": online, "wallets": wallets, "txs": case.crypto_txs.all(),
        "transfer_form": TransferForm(), "online_form": OnlineForm(case=case), "wallet_form": WalletForm(case=case), "tx_form": TxForm(),
        "account_forms": {r.account.pk: AccountForm(instance=r.account, case=case, prefix=f"a{r.account.pk}") for r in rows},
        "banks": BANKS, "channels": CHANNELS, "statuses": BankAccount.Status.choices, "can_reveal": request.user.can("pii.reveal"),
        "linked_cases": sorted({c.pk: c for cs in links.values() for c in cs}.values(), key=lambda c: c.pk),
        "active": "cases",
    }  # fmt: skip
    return render(request, "cyber/case.html", ctx)


@login_required
def flow_json(request, pk):
    case = case_for(request, pk)
    return JsonResponse(money.flow_graph(case, index.links(case)))


# ---------------------------------------------------------------- transfers
@login_required
@require_POST
def transfer_add(request, pk):
    case = case_for(request, pk, edit=True)
    form = TransferForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "เพิ่มธุรกรรมไม่ได้: " + form_errors(form))
        return back(case, "transfers")
    cd = form.cleaned_data
    evidence, source = cd["evidence"], MoneyTransfer.Source.MANUAL
    token = cd.get("slip_token") or ""
    if token and not evidence:
        path = f"{TMP}/{Path(token).name}"
        if default_storage.exists(path):
            with default_storage.open(path, "rb") as f:
                evidence = ContentFile(f.read(), name=f"slip{Path(path).suffix}")
            default_storage.delete(path)
            source = MoneyTransfer.Source.SLIP
    try:
        money.add_transfer(case, request.user, to_no=cd["to_no"], amount=cd["amount"], occurred_at=cd["occurred_at"], from_no=cd["from_no"],
                           from_bank=cd["from_bank"], from_name=cd["from_name"], from_victim=cd["from_victim"], to_bank=cd["to_bank"],
                           to_name=cd["to_name"], channel=cd["channel"], reference=cd["reference"], evidence=evidence, source=source)  # fmt: skip
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return back(case, "transfers")
    messages.success(request, f"เพิ่มธุรกรรม {cd['amount']:,.2f} บาท แล้ว · ยอดเสียหายในบัญชีทรัพย์ปรับให้อัตโนมัติ")
    return back(case, "transfers")


@login_required
@require_POST
def transfer_delete(request, pk, tid):
    case = case_for(request, pk, edit=True)
    money.delete_transfer(get_object_or_404(MoneyTransfer, pk=tid, case=case), request.user)
    messages.success(request, "ลบธุรกรรมแล้ว")
    return back(case, "transfers")


@login_required
def transfer_evidence(request, pk, tid):
    case = case_for(request, pk)
    t = get_object_or_404(MoneyTransfer, pk=tid, case=case)
    if not t.evidence:
        raise Http404
    log(request.user, "download", "money_transfer", t.pk, f"เปิดหลักฐานธุรกรรม คดี {case}")
    return FileResponse(t.evidence.open("rb"), filename=Path(t.evidence.name).name)


@login_required
@require_POST
def slip_read(request, pk):
    """Upload a slip → the add form filled by the unit's AI, labelled for review. Nothing is saved until the officer presses save."""
    case = case_for(request, pk, edit=True)
    up = request.FILES.get("slip")
    if not up:
        return HttpResponse('<p class="chip chip-warning">เลือกไฟล์สลิปก่อน</p>')
    data = up.read()
    token = f"{uuid.uuid4().hex}{Path(up.name).suffix.lower()[:6]}"
    default_storage.save(f"{TMP}/{token}", ContentFile(data))
    initial, error = {"slip_token": token, "from_victim": True}, ""
    try:
        out = services.read_slip(case, request.user, data, up.content_type or "")
        when = None
        if out.get("occurred_at"):
            try:
                when = money.parse_when(out["occurred_at"])
            except ValueError:
                when = None
        initial |= {"occurred_at": when, "amount": out.get("amount") or None, "from_no": out.get("from_account", ""),
                    "from_bank": out.get("from_bank", ""), "from_name": out.get("from_name", ""), "to_no": out.get("to_account", ""),
                    "to_bank": out.get("to_bank", ""), "to_name": out.get("to_name", ""), "reference": out.get("reference", ""),
                    "channel": out.get("channel", "")}  # fmt: skip
    except AIError as e:
        error = str(e)
    form = TransferForm(initial=initial)
    return render(request, "cyber/_transfer_form.html", {"case": case, "transfer_form": form, "from_slip": not error, "slip_error": error,
                                                         "banks": BANKS, "channels": CHANNELS})  # fmt: skip


# ---------------------------------------------------------------- import a bank statement
@login_required
def import_upload(request, pk):
    case = case_for(request, pk, edit=True)
    if request.method == "POST":
        up = request.FILES.get("file")
        if not up or not up.name.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm")):
            messages.error(request, "เลือกไฟล์ Excel (.xlsx) หรือ CSV")
            return redirect("cyber:import", pk=case.pk)
        data = up.read()
        try:
            money.read_table(up.name, data)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect("cyber:import", pk=case.pk)
        token = f"{uuid.uuid4().hex}{Path(up.name).suffix.lower()}"
        default_storage.save(f"{TMP}/{token}", ContentFile(data))
        request.session[f"cyber-import-{case.pk}"] = {"token": token, "name": up.name[:120]}
        return redirect("cyber:import_map", pk=case.pk)
    return render(request, "cyber/import.html", {"case": case, "active": "cases"})


@login_required
def import_map(request, pk):
    case = case_for(request, pk, edit=True)
    info = request.session.get(f"cyber-import-{case.pk}")
    path = f"{TMP}/{Path(info['token']).name}" if info else ""
    if not info or not default_storage.exists(path):
        messages.error(request, "ไม่พบไฟล์ที่อัปโหลด อัปโหลดใหม่อีกครั้ง")
        return redirect("cyber:import", pk=case.pk)
    with default_storage.open(path, "rb") as f:
        headers, rows = money.read_table(info["name"], f.read())
    if request.method == "POST":
        if "cancel" in request.POST:
            default_storage.delete(path)
            request.session.pop(f"cyber-import-{case.pk}", None)
            return back(case, "transfers")
        mapping = {}
        for key, _, _ in money.TARGETS:
            v = request.POST.get(f"map-{key}", "")
            mapping[key] = int(v) if v.isdigit() and int(v) < len(headers) else None
        missing = [label for key, label, req in money.TARGETS if req and mapping[key] is None]
        fixed = {"no": request.POST.get("fixed_no", "").strip(), "bank": request.POST.get("fixed_bank", "").strip(),
                 "name": request.POST.get("fixed_name", "").strip(), "victim": request.POST.get("fixed_victim") == "on"}  # fmt: skip
        if missing:
            messages.error(request, "เลือกคอลัมน์ให้ครบ: " + ", ".join(missing))
        else:
            res = money.import_rows(case, request.user, rows, mapping, fixed if fixed["no"] else None, info["name"])
            default_storage.delete(path)
            request.session.pop(f"cyber-import-{case.pk}", None)
            messages.success(request, f"นำเข้า {res.created} รายการ · ข้าม {res.skipped} (เงินเข้า/ไม่มีบัญชีปลายทาง/ซ้ำ)")
            for err in res.errors[:10]:
                messages.warning(request, err)
            return back(case, "transfers")
        guess = mapping
    else:
        guess = money.guess_mapping(headers)
    ctx = {"case": case, "headers": list(enumerate(headers)), "preview": rows[:8], "count": len(rows), "targets": money.TARGETS,
           "guess": guess, "name": info["name"], "banks": BANKS, "active": "cases"}  # fmt: skip
    return render(request, "cyber/import_map.html", ctx)


# ---------------------------------------------------------------- mule account register
@login_required
@require_POST
def account_update(request, pk, aid):
    case = case_for(request, pk, edit=True)
    acc = get_object_or_404(BankAccount, pk=aid, case=case)
    old = acc.get_status_display()
    form = AccountForm(request.POST, instance=acc, case=case, prefix=f"a{acc.pk}")
    if form.is_valid():
        form.save()
        log(
            request.user, "edit", "bank_account", acc.pk, f"แก้ข้อมูลบัญชี {acc.masked} ({old} → {acc.get_status_display()}) คดี {case}"
        )
        money.sync_property(case)
        messages.success(request, f"บันทึกบัญชี {acc.masked} แล้ว")
    else:
        messages.error(request, form_errors(form))
    return back(case, "accounts")


@login_required
@require_POST
def account_reveal(request, pk, aid):
    case = case_for(request, pk)
    if not request.user.can("pii.reveal"):
        raise PermissionDenied
    acc = get_object_or_404(BankAccount, pk=aid, case=case)
    log(request.user, "reveal", "bank_account", acc.pk, f"ดูเลขบัญชีเต็ม {acc.bank} คดี {case}")
    return HttpResponse(
        format_html('<code class="text-sm">{}</code> <span class="text-xs text-warning-ink">(บันทึกการดูแล้ว)</span>', acc.account_no)
    )


@login_required
@require_POST
def letters(request, pk):
    case = case_for(request, pk, edit=True)
    ids = [int(x) for x in request.POST.getlist("account") if x.isdigit()]
    parse = lambda v: date.fromisoformat(v) if v else None  # noqa: E731
    try:
        data, made = money.bank_letters(case, request.user, ids, request.POST.get("purpose", ""),
                                        parse(request.POST.get("date_from")), parse(request.POST.get("date_to")))  # fmt: skip
    except (ValidationError, ValueError) as e:
        messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
        return back(case, "accounts")
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'หนังสือถึงธนาคาร-{case.pk}-{len(made)}ฉบับ.zip')}"
    return resp


# ---------------------------------------------------------------- online accounts and crypto
@login_required
@require_POST
def online_add(request, pk):
    case = case_for(request, pk, edit=True)
    form = OnlineForm(request.POST, case=case)
    if form.is_valid():
        cd = form.cleaned_data
        try:
            services.add_online(case, request.user, cd["kind"], cd["value"], cd["display_name"], cd["party"], cd["note"])
            messages.success(request, "เพิ่มบัญชีออนไลน์แล้ว")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    else:
        messages.error(request, form_errors(form))
    return back(case, "online")


@login_required
@require_POST
def online_delete(request, pk, oid):
    case = case_for(request, pk, edit=True)
    o = get_object_or_404(OnlineAccount, pk=oid, case=case)
    log(request.user, "delete", "online_account", o.pk, f"ลบ{o.get_kind_display()} คดี {case}")
    o.delete()
    return back(case, "online")


@login_required
@require_POST
def wallet_add(request, pk):
    case = case_for(request, pk, edit=True)
    form = WalletForm(request.POST, case=case)
    if form.is_valid():
        cd = form.cleaned_data
        try:
            services.add_wallet(case, request.user, cd["network"], cd["address"], cd["label"], cd["party"], cd["note"])
            messages.success(request, "เพิ่มกระเป๋าแล้ว")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    else:
        messages.error(request, form_errors(form))
    return back(case, "crypto")


@login_required
@require_POST
def wallet_delete(request, pk, wid):
    case = case_for(request, pk, edit=True)
    w = get_object_or_404(CryptoWallet, pk=wid, case=case)
    log(request.user, "delete", "crypto_wallet", w.pk, f"ลบกระเป๋า {w.get_network_display()} คดี {case}")
    w.delete()
    return back(case, "crypto")


@login_required
@require_POST
def tx_add(request, pk):
    case = case_for(request, pk, edit=True)
    form = TxForm(request.POST)
    if form.is_valid():
        cd = form.cleaned_data
        try:
            services.add_tx(case, request.user, cd["network"], cd["tx_hash"], cd["amount"], cd["asset"], cd["from_address"], cd["to_address"],
                            cd["occurred_at"])  # fmt: skip
            messages.success(request, "เพิ่มธุรกรรมคริปโตแล้ว")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    else:
        messages.error(request, form_errors(form))
    return back(case, "crypto")


@login_required
@require_POST
def tx_delete(request, pk, tid):
    case = case_for(request, pk, edit=True)
    t = get_object_or_404(CryptoTx, pk=tid, case=case)
    log(request.user, "delete", "crypto_tx", t.pk, f"ลบธุรกรรมคริปโต คดี {case}")
    t.delete()
    return back(case, "crypto")


@login_required
@require_POST
def tx_import(request, pk):
    case = case_for(request, pk, edit=True)
    up = request.FILES.get("file")
    if not up:
        messages.error(request, "เลือกไฟล์ CSV")
        return back(case, "crypto")
    try:
        n, errors = services.import_tx_csv(case, request.user, up.read(), request.POST.get("network", ""))
        messages.success(request, f"นำเข้าธุรกรรมคริปโต {n} รายการ")
        for err in errors[:10]:
            messages.warning(request, err)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return back(case, "crypto")


@login_required
def crypto_export(request, pk):
    case = case_for(request, pk)
    try:
        data = services.crypto_export(case, request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return back(case, "crypto")
    resp = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'cryptotrace-{case.pk}.json')}"
    return resp


# ---------------------------------------------------------------- case network (B4)
def network_args(request):
    kind = request.GET.get("kind", "")
    kind = kind if kind in identifiers.KINDS else ""
    parse = lambda v: date.fromisoformat(v) if v else None  # noqa: E731
    try:
        date_from, date_to = parse(request.GET.get("from")), parse(request.GET.get("to"))
    except ValueError:
        date_from = date_to = None
    focus = request.GET.get("case", "")
    return {"kind": kind, "date_from": date_from, "date_to": date_to, "focus": int(focus) if focus.isdigit() else None}


@login_required
def network_page(request):
    args = network_args(request)
    mine = Case.objects.visible_to(request.user).select_related("owner").order_by("-reported_at")[:300]
    focus = Case.objects.visible_to(request.user).filter(pk=args["focus"]).first() if args["focus"] else None
    log(request.user, "view", "network", args["focus"] or "", "เปิดหน้าเครือข่ายคดี")
    ctx = {"kinds": identifiers.KINDS, "args": args, "focus": focus, "my_cases": mine, "query": request.GET.urlencode(),
           "pending": MergeProposal.objects.filter(status="pending").count() if request.user.role == "supervisor" else 0,
           "active": "network"}  # fmt: skip
    return render(request, "cyber/network.html", ctx)


@login_required
def network_data(request):
    return JsonResponse(index.network(request.user, **network_args(request)))


@login_required
def network_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    graph = index.network(request.user, **network_args(request))
    wb = Workbook()
    ws = wb.active
    ws.title = "เครือข่ายคดี"
    ws.append(["ประเภทตัวระบุ", "ตัวระบุ (ปิดบัง)", "พบในกี่คดี", "คดี", "พนักงานสอบสวน", "ที่มาในคดี"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in index.network_rows(graph):
        ws.append(row)
    ws.append([])
    ws.append([f"ส่งออกโดย {request.user.display_name} · ข้อมูลคดีอื่นแสดงเฉพาะเลขคดีและพนักงานสอบสวน"])
    for col, width in zip("ABCDEF", (22, 30, 12, 22, 28, 40), strict=True):
        ws.column_dimensions[col].width = width
    import io

    buf = io.BytesIO()
    wb.save(buf)
    log(request.user, "export", "network", "", f"ส่งออกเครือข่ายคดี Excel {graph['identifiers']} ตัวระบุ {graph['cases']} คดี")
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote('เครือข่ายคดี.xlsx')}"
    return resp


@login_required
@require_POST
def merge_propose(request, pk):
    case = case_for(request, pk)
    if not request.user.can("case.edit"):
        raise PermissionDenied
    other_id = request.POST.get("other", "")
    other = Case.objects.alive().filter(pk=int(other_id)).first() if other_id.isdigit() else None
    if not other:
        messages.error(request, "เลือกคดีที่จะเสนอรวม")
    else:
        try:
            services.propose_merge(case, other, request.user, request.POST.get("reason", ""))
            messages.success(request, f"ส่งคำขอรวมสำนวน {case.label} กับ {other.label} ให้หัวหน้างานสอบสวนแล้ว")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else reverse("cyber:network") + f"?case={case.pk}")


@login_required
def merge_list(request):
    qs = MergeProposal.objects.select_related("case__owner", "other__owner", "proposed_by", "decided_by")
    is_head = request.user.role in ("supervisor", "commander", "admin")
    if not is_head:
        qs = qs.filter(proposed_by=request.user)
    return render(
        request,
        "cyber/merge_list.html",
        {"proposals": qs[:100], "is_head": request.user.role == "supervisor", "active": "network"},
    )


@login_required
@require_POST
def merge_decide(request, pk):
    if request.user.role != "supervisor":
        raise PermissionDenied
    p = get_object_or_404(MergeProposal, pk=pk)
    try:
        services.decide_merge(p, request.user, request.POST.get("decision") == "approve", request.POST.get("note", ""))
        messages.success(request, f"บันทึกผลพิจารณาแล้ว: {p.get_status_display()}")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("cyber:merge_list")
