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


class OfficerProfile(models.Model):
    """Personal section of "ข้อมูลพื้นฐานของฉัน" (A3) beyond the User fields."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile", verbose_name="ผู้ใช้")
    birth_date = models.DateField("วันเกิด", null=True, blank=True)
    signature = models.ImageField("ภาพลายมือชื่อ", upload_to="signatures/", null=True, blank=True)

    class Meta:
        verbose_name = "ข้อมูลส่วนตัวพนักงานสอบสวน"


class Unit(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="unit", verbose_name="ผู้ใช้")
    name = models.CharField("ชื่อสถานี/กลุ่มงาน", max_length=200)
    address = models.CharField("ที่อยู่", max_length=400)
    doc_prefix = models.CharField("เลขที่หนังสือ (ส่วนหน้า)", max_length=60)
    division = models.CharField("สังกัด บก.", max_length=200, blank=True)
    bureau = models.CharField("สังกัด บช.", max_length=200, blank=True)
    phone = models.CharField("โทรศัพท์หน่วย", max_length=40, blank=True)

    class Meta:
        verbose_name = "หน่วยงาน"


class Commander(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="commanders", verbose_name="ผู้ใช้")
    rank = models.CharField("ยศ", max_length=40)
    name = models.CharField("ชื่อ-สกุล", max_length=200)
    position = models.CharField("ตำแหน่ง", max_length=200)
    is_default = models.BooleanField("ผู้ลงนามหลัก", default=False)

    class Meta:
        verbose_name = "ผู้บังคับบัญชา"
        ordering = ["-is_default", "id"]

    def __str__(self):
        return f"{self.rank}{self.name}"


class ArrestTeamMember(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="arrest_team", verbose_name="ผู้ใช้")
    rank = models.CharField("ยศ", max_length=40)
    name = models.CharField("ชื่อ-สกุล", max_length=200)
    position = models.CharField("ตำแหน่ง", max_length=200, blank=True)
    phone = models.CharField("โทรศัพท์", max_length=40, blank=True)

    class Meta:
        verbose_name = "เจ้าหน้าที่ชุดจับกุม"
        ordering = ["id"]

    def __str__(self):
        return f"{self.rank}{self.name}"


class CourtOrProsecutor(models.Model):
    class Kind(models.TextChoices):
        COURT = "court", "ศาล"
        PROSECUTOR = "prosecutor", "สำนักงานอัยการ"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="courts", verbose_name="ผู้ใช้")
    kind = models.CharField("ประเภท", max_length=20, choices=Kind.choices)
    name = models.CharField("ชื่อ", max_length=200)
    court_type = models.CharField("ประเภทศาล", max_length=40, blank=True, help_text="เช่น ศาลแขวง ศาลจังหวัด")

    class Meta:
        verbose_name = "ศาล/อัยการ"
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name
