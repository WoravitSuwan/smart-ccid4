import secrets

from django.conf import settings
from django.db import models


def new_token(n=16) -> str:
    return secrets.token_urlsafe(n)


def new_reference() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
    return "CC-" + "".join(secrets.choice(alphabet) for _ in range(8))


class IntakeKind(models.TextChoices):
    CRIMINAL = "criminal", "คดีอาญาทั่วไป"
    ONLINE = "online", "คดีอาชญากรรมทางเทคโนโลยี"


class IntakeLink(models.Model):
    """ลิงก์และ QR ส่วนตัวของพนักงานสอบสวน (A2.1): เรื่องที่เข้ามาทางลิงก์นี้เข้าบัญชีเจ้าของ."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="intake_links")
    token = models.CharField(max_length=40, unique=True, default=new_token)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class IntakeSubmission(models.Model):
    """เรื่องที่ประชาชนแจ้งเข้ามา: หน้าสาธารณะเขียนได้อย่างเดียว ระบบภายในดึงไปตั้งเป็นคดี."""

    class Status(models.TextChoices):
        PENDING = "pending", "รอระบบประมวลผล"
        CASE_CREATED = "case_created", "ตั้งเป็นคดีแล้ว"
        FAILED = "failed", "ประมวลผลไม่สำเร็จ"
        REJECTED = "rejected", "ไม่รับเรื่อง"

    class Mode(models.TextChoices):
        CHAT = "chat", "แชทกับผู้ช่วย"
        FORM = "form", "ฟอร์มทีละขั้น"

    link = models.ForeignKey(IntakeLink, on_delete=models.PROTECT, related_name="submissions")
    kind = models.CharField("ประเภท", max_length=10, choices=IntakeKind.choices)
    mode = models.CharField("วิธีแจ้ง", max_length=10, choices=Mode.choices)
    reference = models.CharField("เลขอ้างอิง", max_length=20, unique=True, default=new_reference)
    track_token = models.CharField(max_length=40, unique=True, default=new_token)
    answers = models.JSONField("คำตอบตามหัวข้อ", default=dict)
    chat = models.JSONField("ประวัติแชท", default=list)
    extracted = models.JSONField("ข้อมูลที่ AI แยกได้", default=dict, blank=True)
    consent_text = models.TextField("ข้อความยินยอม")
    consent_at = models.DateTimeField("ยินยอมเมื่อ")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                              verbose_name="เจ้าหน้าที่ที่กรอกแทน")  # fmt: skip
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.CharField(max_length=300, blank=True)
    case = models.OneToOneField("cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="intake")
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "เรื่องรับแจ้งออนไลน์"
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


def intake_path(instance, filename):
    return f"intake/{instance.submission.reference}/{filename}"


class IntakeFile(models.Model):
    """ไฟล์แนบจากผู้แจ้ง (ภาพแชท สลิป บัตร) เก็บพร้อม SHA-256 · ย้ายเข้าทะเบียนพยานหลักฐานในเฟส 10."""

    submission = models.ForeignKey(IntakeSubmission, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=intake_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    note = models.CharField("คำอธิบาย", max_length=200, blank=True)


def import_path(instance, filename):
    return f"imports/{instance.created_by_id}/{filename}"


class ArrestImport(models.Model):
    """นำเข้าคดีจากบันทึกจับกุม/บันทึกประจำวัน (A2.3): ไฟล์ → OCR → AI สกัด → ตรวจทาน → สร้างคดี."""

    class Status(models.TextChoices):
        REVIEW = "review", "รอตรวจทาน"
        DONE = "done", "สร้างคดีแล้ว"
        FAILED = "failed", "อ่านไม่สำเร็จ"

    class Source(models.TextChoices):
        ARREST = "arrest", "บันทึกจับกุม"
        TRAFFIC = "traffic", "บันทึกประจำวันคดีจราจร"

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ARREST)
    file = models.FileField(upload_to=import_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    ocr_text = models.TextField(blank=True)
    extracted = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REVIEW)
    error = models.CharField(max_length=300, blank=True)
    case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class TrackingLink(models.Model):
    """ลิงก์ติดตามสถานะของผู้แจ้ง (B8): token สุ่ม · หมดอายุได้ · ยกเลิกได้ · เห็นเฉพาะสถานะและข้อความที่พนักงานสอบสวนเผยแพร่."""

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="tracking_links", verbose_name="คดี")
    token = models.CharField(max_length=40, unique=True, default=new_token)
    reference = models.CharField("เลขอ้างอิงที่ผู้แจ้งเห็น", max_length=30)
    expires_at = models.DateTimeField("หมดอายุ")
    revoked_at = models.DateTimeField("ยกเลิกเมื่อ", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ลิงก์ติดตามของผู้แจ้ง"
        verbose_name_plural = "ลิงก์ติดตามของผู้แจ้ง"
        ordering = ["-created_at"]

    @property
    def active(self) -> bool:
        from django.utils import timezone

        return self.revoked_at is None and self.expires_at > timezone.now()
