from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cases.views import can_edit, get_case
from parties.models import CaseParty

from . import services
from .forms import DetentionForm, TaskForm
from .models import Detention, Task


def editable_case(request, pk):
    case = get_case(request.user, pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    return case


def panel_response(request, case):
    ctx = services.panel_context(case) | {
        "case": case,
        "editable": True,
        "all_tasks": list(case.tasks.select_related("done_by", "created_by").order_by("-created_at")),
    }
    return render(request, "deadlines/_panel_oob.html", ctx)


@login_required
@require_POST
def task_add(request, pk):
    case = editable_case(request, pk)
    form = TaskForm(request.POST)
    if form.is_valid():
        services.add_task(case, request.user, **form.cleaned_data)
        messages.success(request, "เพิ่มรายการแล้ว")
    else:
        messages.error(request, "พิมพ์รายการก่อนกดเพิ่ม")
    return redirect(f"{case.get_absolute_url()}#{request.POST.get('back', 'investigator')}")


@login_required
@require_POST
def task_toggle(request, pk, task_id):
    case = editable_case(request, pk)
    task = get_object_or_404(Task, pk=task_id, case=case)
    services.set_done(task, request.user, not task.is_done)
    if request.headers.get("HX-Request"):
        return panel_response(request, case)
    return redirect(case.get_absolute_url())


@login_required
@require_POST
def task_share(request, pk, task_id):
    """Show/hide a done item on the reporter's tracking page (B8)."""
    case = editable_case(request, pk)
    task = get_object_or_404(Task, pk=task_id, case=case, done_at__isnull=False)
    task.show_to_complainant = not task.show_to_complainant
    task.save(update_fields=["show_to_complainant"])
    from audit.services import log

    log(request.user, "edit", "task", task.pk, f"{'เปิด' if task.show_to_complainant else 'ปิด'}ให้ผู้แจ้งเห็น: {task.title} คดี {case}")
    if request.headers.get("HX-Request"):
        return panel_response(request, case)
    return redirect(case.get_absolute_url())


@login_required
@require_POST
def task_delete(request, pk, task_id):
    case = editable_case(request, pk)
    task = get_object_or_404(Task, pk=task_id, case=case, source=Task.Source.MANUAL)
    from audit.services import log

    log(request.user, "delete", "task", task.pk, f"ลบรายการ '{task.title}' คดี {case}")
    task.delete()
    if request.headers.get("HX-Request"):
        return panel_response(request, case)
    return redirect(case.get_absolute_url())


@login_required
@require_POST
def detention_add(request, pk, party_id):
    case = editable_case(request, pk)
    party = get_object_or_404(CaseParty.objects.select_related("person"), pk=party_id, case=case, role="suspect")
    form = DetentionForm(request.POST, prefix=f"det{party.pk}")
    if form.is_valid():
        try:
            d = form.cleaned_data
            services.record_detention(request.user, case, party, d["start_date"], d["days"], d["court"], d["order_no"], d["note"])
            messages.success(request, "บันทึกแล้ว ระบบคำนวณกำหนดเวลาใหม่")
        except ValidationError as e:
            messages.error(request, " · ".join(e.messages))
    else:
        messages.error(
            request, "ข้อมูลการฝากขังไม่ครบ: " + " ".join(f"{form.fields[k].label} {v[0]}" for k, v in form.errors.items())
        )
    return redirect(f"{case.get_absolute_url()}#suspects")


@login_required
@require_POST
def detention_delete(request, pk, det_id):
    case = editable_case(request, pk)
    det = get_object_or_404(Detention, pk=det_id, party__case=case)
    services.delete_detention(request.user, det)
    messages.success(request, "ลบรายการแล้ว")
    if request.headers.get("HX-Request"):
        from django.http import HttpResponse

        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return redirect(f"{case.get_absolute_url()}#suspects")
