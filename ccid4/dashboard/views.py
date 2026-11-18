from datetime import date
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from audit.services import log
from core.convert import soffice_available

from . import charts, report, services
from .models import DigestSetting

UNIT_ROLES = ("supervisor", "commander", "admin")


def pick_month(request) -> date | None:
    v = request.GET.get("month", "")
    try:
        y, m = (int(x) for x in v.split("-"))
        return date(y, m, 1)
    except ValueError:
        return None


@login_required
def unit(request):
    if request.user.role not in UNIT_ROLES:
        raise PermissionDenied
    data = services.unit(request.user, pick_month(request))
    log(request.user, "view", "dashboard", "", f"เปิดแดชบอร์ดหน่วยงาน {data['month_label']}")
    ctx = data | {
        "monthly_chart": charts.columns([(label, n) for label, n, _ in data["monthly"]]),
        "offense_chart": charts.hbars(data["offenses"]),
        "month_value": data["month"].strftime("%Y-%m"), "pdf_available": soffice_available(),
        "can_open_cases": request.user.role != "admin", "active": "unit",
    }  # fmt: skip
    return render(request, "dashboard/unit.html", ctx)


@login_required
def export(request, fmt):
    if request.user.role not in UNIT_ROLES:
        raise PermissionDenied
    data = services.unit(request.user, pick_month(request))
    name = f"รายงานประจำเดือน-{data['month'].strftime('%Y-%m')}"
    if fmt == "xlsx":
        body, ctype = report.excel(data, request.user), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pdf":
        if not soffice_available():
            messages.error(request, "เครื่องนี้ไม่มี LibreOffice จึงส่งออก PDF ไม่ได้ ใช้ Excel แทน")
            return redirect(f"/dashboard/?month={data['month'].strftime('%Y-%m')}")
        body, ctype = report.pdf(data, request.user), "application/pdf"
    else:
        raise PermissionDenied
    log(request.user, "export", "dashboard", "", f"ส่งออกรายงานประจำเดือน {data['month_label']} ({fmt.upper()})")
    resp = HttpResponse(body, content_type=ctype)
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name + '.' + fmt)}"
    return resp


@login_required
@require_POST
def digest_setting(request):
    s, _ = DigestSetting.objects.get_or_create(user=request.user)
    s.enabled = request.POST.get("enabled") == "on"
    s.email = request.POST.get("email", "").strip()[:254] or request.user.email
    if s.enabled and not s.email:
        messages.error(request, "ใส่อีเมลในระบบของหน่วยก่อนเปิดสรุปรายวัน")
        return redirect("core:home")
    s.save()
    request.user.email = request.user.email or s.email
    request.user.save(update_fields=["email"])
    log(request.user, "edit", "digest", request.user.pk, f"{'เปิด' if s.enabled else 'ปิด'}สรุปรายวันทางอีเมล")
    messages.success(request, "บันทึกการตั้งค่าสรุปรายวันแล้ว")
    return redirect("core:home")
