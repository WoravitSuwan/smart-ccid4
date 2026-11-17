from django.conf import settings

from .menu import menu_for


def ui(request):
    user = getattr(request, "user", None)
    authed = bool(user and user.is_authenticated)
    return {
        "UNIT_SHORT": settings.UNIT_SHORT,
        "UNIT_NAME": settings.UNIT_NAME,
        "DEMO_MODE": settings.DEMO_MODE,
        "THEME": getattr(user, "theme", "dark") if authed else request.COOKIES.get("theme", "dark"),
        "MENU": menu_for(user) if authed else [],
        "PROFILE_MISSING": _profile_missing(user) if authed else 0,
        "UNREAD": _unread(user) if authed else 0,
    }


def _profile_missing(user):
    from accounts.services import completeness

    return completeness(user).missing_count


def _unread(user):
    from .notify import unread_count

    return unread_count(user)
