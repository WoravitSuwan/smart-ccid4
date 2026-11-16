from django.contrib.auth.backends import ModelBackend


class ExpiringModelBackend(ModelBackend):
    """Expired accounts cannot sign in (an administrator extends the date)."""

    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and not getattr(user, "is_expired", False)
