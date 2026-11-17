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


class CustomDocType(models.Model):
    """เอกสารใหม่ที่หน่วยเพิ่มเอง (B11) — ไม่ต้องขอผ่านแจ้งปัญหา."""

    PER_CHOICES = [("", "ฉบับเดียวต่อคดี"), ("accuser", "รายผู้กล่าวหา"), ("suspect", "รายผู้ต้องหา"), ("witness", "รายพยาน"),
                   ("people", "รายผู้กล่าวหา/พยาน")]  # fmt: skip
    key = models.CharField("รหัส", max_length=60, unique=True)
    title = models.CharField("ชื่อเอกสาร", max_length=300)
    group = models.CharField("กลุ่ม", max_length=60)
    per = models.CharField("ออกเป็น", max_length=10, choices=PER_CHOICES, blank=True)
    active = models.BooleanField("ใช้งาน", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "เอกสารที่หน่วยเพิ่มเอง"
        verbose_name_plural = "เอกสารที่หน่วยเพิ่มเอง"
        ordering = ["id"]


class TemplateVersion(models.Model):
    """เทมเพลตที่อัปโหลด (B11): มีเวอร์ชัน · จับคู่ placeholder กับข้อมูลในระบบ · เอกสารที่ออกแล้วอ้างอิงเวอร์ชันที่ใช้."""

    key = models.CharField("แบบเอกสาร", max_length=60, db_index=True)
    version = models.PositiveIntegerField("เวอร์ชัน")
    file = models.FileField("ไฟล์ .docx", upload_to="templates/%Y/")
    sha256 = models.CharField(max_length=64)
    placeholders = models.JSONField("ช่องในไฟล์", default=list)
    mapping = models.JSONField("จับคู่กับข้อมูลในระบบ", default=dict, blank=True)  # placeholder → path, "" = เว้นว่างให้เขียนเอง
    digits = models.CharField("ตัวเลข", max_length=10, choices=[("thai", "เลขไทย"), ("arabic", "เลขอารบิก")], default="thai")
    note = models.CharField("หมายเหตุการเปลี่ยนแปลง", max_length=300, blank=True)
    active = models.BooleanField("ใช้อยู่", default=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "เวอร์ชันเทมเพลต"
        verbose_name_plural = "เวอร์ชันเทมเพลต"
        ordering = ["key", "-version"]
        constraints = [models.UniqueConstraint(fields=["key", "version"], name="documents_template_version_once")]

    def __str__(self):
        return f"{self.key} v{self.version}"

    @property
    def label(self) -> str:
        return f"v{self.version}-{self.sha256[:8]}"


class CaseBundle(models.Model):
    """สำนวนรวมเป็น PDF เล่มเดียว (B12): สารบัญ เลขหน้า ลายน้ำผู้ส่งออก."""

    class Status(models.TextChoices):
        PENDING = "pending", "รอคิว"
        DONE = "done", "เสร็จ"
        FAILED = "failed", "ไม่สำเร็จ"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="bundles", verbose_name="คดี")
    request = models.JSONField("รายการที่เลือก (ตามลำดับ)", default=list)
    items = models.JSONField("สารบัญ", default=list, blank=True)  # [{"title", "start", "pages"}]
    file = models.FileField(upload_to=doc_path, blank=True)
    pages = models.PositiveIntegerField("จำนวนหน้า", default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "สำนวนรวม PDF"
        verbose_name_plural = "สำนวนรวม PDF"
        ordering = ["-created_at"]
