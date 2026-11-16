from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.services import log

from .models import Case, CaseStatus, CaseType


def summary_counts(qs):
    agg = qs.aggregate(
        total=Count("id"),
        received=Count("id", filter=Q(status__in=[CaseStatus.RECEIVED, CaseStatus.IMPORTED])),
        investigating=Count("id", filter=Q(status=CaseStatus.INVESTIGATING)),
        closed=Count("id", filter=Q(status=CaseStatus.CLOSED)),
    )
    return agg


def filter_cases(qs, q="", status="", ctype=""):
    if q:
        qs = qs.filter(
            Q(case_no__icontains=q)
            | Q(receipt_no__icontains=q)
            | Q(offense__icontains=q)
            | Q(title__icontains=q)
            | Q(parties__person__first_name__icontains=q)
            | Q(parties__person__last_name__icontains=q)
        ).distinct()
    if status == "received":
        qs = qs.filter(status__in=[CaseStatus.RECEIVED, CaseStatus.IMPORTED])
    elif status in CaseStatus.values:
        qs = qs.filter(status=status)
    if ctype in CaseType.values:
        qs = qs.filter(case_type=ctype)
    return qs


@transaction.atomic
def create_case(user, **fields) -> Case:
    case = Case.objects.create(owner=user, **fields)
    log(user, "create", "case", case.pk, f"สร้างคดี {case}")
    return case


@transaction.atomic
def soft_delete(user, case: Case, reason: str) -> None:
    case.deleted_at = timezone.now()
    case.deleted_by = user
    case.save(update_fields=["deleted_at", "deleted_by"])
    log(user, "delete", "case", case.pk, f"ลบคดี {case} (ลบแบบกู้คืนได้) เหตุผล: {reason}")


def changed_labels(form) -> list[str]:
    return [str(form.fields[f].label) for f in form.changed_data if f in form.fields]


@transaction.atomic
def save_case(
    user, case: Case, case_form, accuser_form, property_fs, suspect_fs, witness_fs, circumstances_source="manual"
) -> list[str]:
    """Save every tab of the case page in one transaction (A4.1). Returns a short list of what changed."""
    from parties import services as party_services

    summary = []
    if case_form.has_changed():
        case_form.save()
        if "circumstances" in case_form.changed_data and case.circumstances.strip():
            from django.db.models import Max

            from .models import CircumstanceVersion

            last = case.circumstance_versions.aggregate(m=Max("version"))["m"] or 0
            CircumstanceVersion.objects.create(
                case=case, version=last + 1, text=case.circumstances, source=circumstances_source, created_by=user
            )
        log(user, "edit", "case", case.pk, f"แก้ไขข้อมูลคดี {case}: {', '.join(changed_labels(case_form))}")
        summary.append("ข้อมูลคดี")
    if accuser_form.has_changed():
        party_services.save_party(user, case, "accuser", 1, accuser_form.cleaned_data)
        summary.append("ผู้กล่าวหา")
    if property_fs.has_changed():
        for obj in property_fs.save(commit=False):
            obj.case = case
            obj.save()
        for obj in property_fs.deleted_objects:
            obj.delete()
        log(user, "edit", "case", case.pk, f"แก้ไขบัญชีทรัพย์คดี {case}")
        summary.append("บัญชีทรัพย์")
    for role, fs, label in (("suspect", suspect_fs, "ผู้ต้องหา"), ("witness", witness_fs, "พยาน")):
        if fs.has_changed():
            order = 0
            for form in fs.forms:
                if not form.cleaned_data:
                    continue
                if party_services.save_party(user, case, role, order + 1, form.cleaned_data):
                    order += 1
            summary.append(label)
    if summary:
        case.save(update_fields=["updated_at"])
    from deadlines.services import sync_rule_tasks

    sync_rule_tasks(case)  # A4.1: recompute tasks and deadlines on every save
    return summary
