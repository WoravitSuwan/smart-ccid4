from django import template
from django.utils import timezone

from core import thai

register = template.Library()


def _local(value):
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return timezone.localtime(value)
    return value


@register.filter
def be_date(value, style="short"):
    """{{ d|be_date }} → 12 พ.ย. 2569 · {{ d|be_date:"long" }} · {{ d|be_date:"doc" }} (Thai digits)."""
    return thai.format_date(_local(value), style) if value else ""


@register.filter
def be_datetime(value):
    if not value:
        return ""
    v = _local(value)
    return f"{thai.format_date(v)} {thai.format_time(v)}"


@register.filter
def thai_digits(value):
    return thai.to_thai_digits(value)


@register.filter
def mask_id(value):
    return thai.mask_citizen_id(value)


@register.filter
def mask_account(value):
    return thai.mask_account(value)


@register.filter
def baht(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return ""
