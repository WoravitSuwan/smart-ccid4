"""หนังสือออกภายนอก (A4.6)."""

from datetime import date, datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.forms import StyledMixin
from audit.services import log
from deadlines import services as dl_services

from .letters import LETTER_TEMPLATES
from .models import OutgoingLetter
from .views import can_edit, get_case

WIDGETS = {
    "text": lambda label: forms.CharField(label=label, required=False, max_length=300),
    "textarea": lambda label: forms.CharField(label=label, required=False, widget=forms.Textarea(attrs={"rows": 3})),
    "date": lambda label: forms.DateField(
        label=label, required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    ),
    "datetime-local": lambda label: forms.DateTimeField(
        label=label, required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
    ),
    "checkbox": lambda label: forms.BooleanField(label=label, required=False),
}


def letter_form_class(template_key):
    tpl = LETTER_TEMPLATES[template_key]
    attrs = {
        "subject": forms.CharField(label="เรื่อง", max_length=300, initial=tpl["subject"]),
        "recipient": forms.CharField(label="เรียน", max_length=300, initial=tpl["recipient"]),
        "number": forms.CharField(
            label="เลขที่หนังสือ", max_length=60, required=False, help_text="เว้นว่างได้ ระบบออกเลขให้เมื่อกด 'ออกหนังสือ'"
        ),
    }
    for key, label, kind in tpl["fields"]:
        attrs[f"f_{key}"] = WIDGETS[kind](label)
    return type("LetterForm", (StyledMixin, forms.Form), attrs)


class ReplyForm(StyledMixin, forms.Form):
    replied_at = forms.DateField(label="วันที่ได้รับหนังสือตอบ", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    reply_file = forms.FileField(label="แนบไฟล์หนังสือตอบ (PDF หรือภาพ)", required=False)
    reply_note = forms.CharField(label="สรุปผลที่ได้รับ", max_length=500, required=False)

    def clean_reply_file(self):
        f = self.cleaned_data.get("reply_file")
        if f:
            if f.size > 20 * 1024 * 1024:
                raise forms.ValidationError("ไฟล์ใหญ่เกิน 20 MB")
            if not f.name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
                raise forms.ValidationError("รับเฉพาะ PDF, JPG, PNG")
        return f


def jsonable(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k.startswith("f_"):
            out[k[2:]] = v.isoformat() if isinstance(v, date | datetime) else v
    return out


def initial_from(letter):
    init = {"subject": letter.subject, "recipient": letter.recipient, "number": letter.number}
    init.update({f"f_{k}": v for k, v in letter.fields.items()})
    return init


def editable(request, pk):
    case = get_case(request.user, pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    return case


def next_number(case) -> str:
    unit = getattr(case.owner, "unit", None)
    prefix = unit.doc_prefix if unit and unit.doc_prefix else "ตช."
    year = timezone.localdate().year + 543
    n = (
        OutgoingLetter.objects.filter(case__owner=case.owner, issued_at__year=timezone.localdate().year)
        .exclude(number="")
        .count()
        + 1
    )
    return f"{prefix}/{n}/{year}"


@login_required
def letter_new(request, pk):
    case = editable(request, pk)
    key = request.GET.get("t") or request.POST.get("t")
    if key not in LETTER_TEMPLATES:
        return render(request, "cases/letter_pick.html", {"case": case, "templates": LETTER_TEMPLATES, "active": "cases"})
    Form = letter_form_class(key)
    form = Form(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            letter = OutgoingLetter.objects.create(
                case=case, template=key, subject=d["subject"], recipient=d["recipient"], number=d["number"],
                fields=jsonable(d), created_by=request.user,
            )  # fmt: skip
            log(request.user, "create", "letter", letter.pk, f"ร่างหนังสือ '{letter.subject}' คดี {case}")
            dl_services.log_action(case, request.user, f"ร่างหนังสือ: {letter.subject} ถึง {letter.recipient}")
            dl_services.sync_rule_tasks(case)
        messages.success(request, "บันทึกร่างหนังสือแล้ว")
        return redirect(f"{case.get_absolute_url()}#investigator")
    ctx = {"case": case, "form": form, "tpl": LETTER_TEMPLATES[key], "key": key, "active": "cases"}
    return render(request, "cases/letter_form.html", ctx)


@login_required
def letter_edit(request, pk, letter_id):
    case = get_case(request.user, pk)
    letter = get_object_or_404(OutgoingLetter, pk=letter_id, case=case)
    can = can_edit(request.user, case)
    Form = letter_form_class(letter.template)
    form = Form(request.POST if request.POST.get("action") == "save" else None, initial=initial_from(letter))
    reply = ReplyForm(
        request.POST if request.POST.get("action") == "reply" else None,
        request.FILES if request.POST.get("action") == "reply" else None,
        initial={"replied_at": letter.replied_at or timezone.localdate(), "reply_note": letter.reply_note},
    )
    if request.method == "POST":
        if not can:
            raise PermissionDenied
        action = request.POST.get("action")
        back = redirect(f"{case.get_absolute_url()}#investigator")
        if action == "save" and form.is_valid():
            d = form.cleaned_data
            letter.subject, letter.recipient, letter.number, letter.fields = (
                d["subject"],
                d["recipient"],
                d["number"],
                jsonable(d),
            )
            letter.save()
            log(request.user, "edit", "letter", letter.pk, f"แก้หนังสือ '{letter.subject}' คดี {case}")
            messages.success(request, "บันทึกหนังสือแล้ว")
            return back
        if action == "issue" and letter.status == OutgoingLetter.Status.DRAFT:
            with transaction.atomic():
                letter.number = letter.number or next_number(case)
                letter.status, letter.issued_at = OutgoingLetter.Status.ISSUED, timezone.localdate()
                letter.save()
                log(request.user, "edit", "letter", letter.pk, f"ออกหนังสือ {letter.number} คดี {case}")
                dl_services.log_action(case, request.user, f"ออกหนังสือ {letter.number}: {letter.subject}")
            messages.success(request, f"ออกหนังสือเลขที่ {letter.number} แล้ว")
            return back
        if action == "reply" and letter.status != OutgoingLetter.Status.DRAFT and reply.is_valid():
            d = reply.cleaned_data
            with transaction.atomic():
                letter.status, letter.replied_at, letter.reply_note = (
                    OutgoingLetter.Status.REPLIED,
                    d["replied_at"],
                    d["reply_note"],
                )
                if d["reply_file"]:
                    letter.reply_file = d["reply_file"]
                letter.save()
                log(request.user, "edit", "letter", letter.pk, f"ได้รับหนังสือตอบ {letter.number} คดี {case}")
                dl_services.log_action(
                    case, request.user, f"ได้รับหนังสือตอบ {letter.number}: {letter.subject}", reason=d["reply_note"]
                )
            messages.success(request, "บันทึกการได้รับหนังสือตอบแล้ว")
            return back
        if action == "delete" and letter.status == OutgoingLetter.Status.DRAFT:
            log(request.user, "delete", "letter", letter.pk, f"ลบร่างหนังสือ '{letter.subject}' คดี {case}")
            letter.delete()
            dl_services.sync_rule_tasks(case)
            messages.success(request, "ลบร่างหนังสือแล้ว")
            return back
        messages.error(request, "ทำรายการไม่ได้ ตรวจข้อมูลอีกครั้ง")
    if not can:
        for f in form.fields.values():
            f.disabled = True
    ctx = {
        "case": case,
        "letter": letter,
        "form": form,
        "reply": reply,
        "tpl": LETTER_TEMPLATES[letter.template],
        "editable": can,
        "active": "cases",
    }
    return render(request, "cases/letter_form.html", ctx)


@login_required
def letter_reply_file(request, pk, letter_id):
    case = get_case(request.user, pk)
    letter = get_object_or_404(OutgoingLetter, pk=letter_id, case=case)
    if not letter.reply_file:
        raise Http404
    log(request.user, "download", "letter", letter.pk, f"ดาวน์โหลดหนังสือตอบ {letter.number} คดี {case}")
    return FileResponse(letter.reply_file.open("rb"), as_attachment=True, filename=letter.reply_file.name.rsplit("/", 1)[-1])
