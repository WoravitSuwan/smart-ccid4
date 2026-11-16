from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    INVESTIGATOR = "investigator", "พนักงานสอบสวน"
    ASSISTANT = "assistant", "ผู้ช่วยพนักงานสอบสวน"
    SUPERVISOR = "supervisor", "หัวหน้างานสอบสวน"
    COMMANDER = "commander", "ผู้บังคับบัญชา"
    CLERK = "clerk", "ธุรการ"
    ADMIN = "admin", "ผู้ดูแลระบบ"


class Theme(models.TextChoices):
    DARK = "dark", "มืด"
    LIGHT = "light", "สว่าง"


# What each role may do. Views and services check these instead of role names.
PERMISSIONS = {
    "case.view_own": {"investigator", "assistant", "supervisor"},
    "case.view_group": {"supervisor"},
    "case.view_all_readonly": {"commander"},
    "case.edit": {"investigator", "assistant", "supervisor"},
    "case.transfer": {"supervisor"},
    "case.close": {"investigator", "supervisor"},
    "statement.confirm": {"investigator", "supervisor"},
    "document.generate": {"investigator", "assistant", "supervisor", "clerk"},
    "summons.batch": {"clerk", "supervisor"},
    "audio.listen": {"investigator", "supervisor"},
    "pii.reveal": {"investigator", "supervisor"},
    "dashboard.unit": {"supervisor", "commander", "admin"},
    "audit.view": {"supervisor", "commander", "admin"},
    "system.manage": {"admin"},
}


class User(AbstractUser):
    role = models.CharField("บทบาท", max_length=20, choices=Role.choices, default=Role.INVESTIGATOR)
    rank = models.CharField("ยศ", max_length=40, blank=True)
    position = models.CharField("ตำแหน่ง", max_length=120, blank=True)
    phone = models.CharField("โทรศัพท์", max_length=40, blank=True)
    unit_group = models.CharField("กลุ่มงาน", max_length=120, blank=True)
    theme = models.CharField("ธีม", max_length=10, choices=Theme.choices, default=Theme.DARK)
    account_expires_at = models.DateField("วันหมดอายุบัญชี", null=True, blank=True)
    require_2fa = models.BooleanField("บังคับยืนยันตัวตน 2 ขั้น", default=False)

    class Meta:
        verbose_name = "ผู้ใช้"
        verbose_name_plural = "ผู้ใช้"

    def save(self, *args, **kwargs):
        self.is_staff = self.role == Role.ADMIN or self.is_superuser
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip() or self.username
        return f"{self.rank}{name}" if self.rank else name

    @property
    def is_expired(self) -> bool:
        return bool(self.account_expires_at and self.account_expires_at < timezone.localdate())

    def can(self, perm: str) -> bool:
        return self.is_active and self.role in PERMISSIONS.get(perm, set())

    def needs_2fa(self) -> bool:
        from django.conf import settings

        return self.require_2fa or self.role == Role.ADMIN or getattr(settings, "TWOFA_REQUIRED_FOR_ALL", False)
