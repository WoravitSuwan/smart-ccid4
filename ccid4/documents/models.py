from django.conf import settings
from django.db import models


def doc_path(instance, filename):
    return f"documents/{instance.case_id}/{filename}"


class DocumentJob(models.Model):
    """One click of 'ออกเอกสาร': several documents × people, built by a Celery worker."""

    class Status(models.TextChoices):
        PENDING = "pending", "รอคิว"
        RUNNING = "running", "กำลังสร้าง"
        DONE = "done", "เสร็จ"
        FAILED = "failed", "ไม่สำเร็จ"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="document_jobs")
    request = models.JSONField("รายการที่ขอ", default=list)  # [{"key": "...", "items": [ids]}]
    formats = models.CharField("รูปแบบ", max_length=10, default="docx")  # docx | pdf | both
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    bundle = models.FileField("ไฟล์รวม", upload_to=doc_path, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class IssuedDocument(models.Model):
    """ทุกการออกเอกสารเก็บไฟล์ ฉบับที่ เทมเพลตที่ใช้ และผู้ออก (A6.1)."""

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="issued_documents", verbose_name="คดี")
    job = models.ForeignKey(DocumentJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    key = models.CharField("แบบเอกสาร", max_length=60)
    title = models.CharField("ชื่อเอกสาร", max_length=300)
    party = models.ForeignKey("parties.CaseParty", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    letter = models.ForeignKey("cases.OutgoingLetter", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    item_label = models.CharField("ออกให้", max_length=300, blank=True)
    version = models.PositiveIntegerField("ฉบับที่", default=1)
    template_version = models.CharField("รุ่นเทมเพลต", max_length=40)
    docx = models.FileField(upload_to=doc_path)
    pdf = models.FileField(upload_to=doc_path, blank=True)
    status = models.CharField(max_length=10, default="done")
    missing_fields = models.JSONField("ช่องที่ยังว่าง", default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "เอกสารที่ออกแล้ว"
        verbose_name_plural = "เอกสารที่ออกแล้ว"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ฉบับที่ {self.version}"
