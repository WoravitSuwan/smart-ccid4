import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def evidence_path(instance, filename):
    return f"evidence/{instance.case_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()[:10]}"


class Exhibit(models.Model):
    """ทะเบียนของกลาง (B6)."""

    class Status(models.TextChoices):
        HELD = "held", "เก็บรักษาที่หน่วย"
        LAB = "lab", "ส่งตรวจพิสูจน์"
        PROSECUTOR = "prosecutor", "ส่งพนักงานอัยการพร้อมสำนวน"
        RETURNED = "returned", "คืนเจ้าของ"
        FORFEITED = "forfeited", "ริบ/ทำลายตามคำพิพากษา"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="exhibits", verbose_name="คดี")
    order = models.PositiveIntegerField("ลำดับ", default=1)
    name = models.CharField("รายการของกลาง", max_length=300)
    quantity = models.CharField("จำนวน", max_length=60, default="1")
    unit = models.CharField("หน่วยนับ", max_length=40, blank=True, help_text="เช่น เครื่อง ใบ เล่ม")
    condition = models.CharField("สภาพ", max_length=300, blank=True)
    storage = models.CharField("ที่เก็บ", max_length=200, blank=True)
    status = models.CharField("สถานะ", max_length=20, choices=Status.choices, default=Status.HELD)
    source = models.CharField("ได้มาจาก/ตรวจยึดจาก", max_length=300, blank=True)
    seized_at = models.DateTimeField("วันเวลาตรวจยึด", null=True, blank=True)
    party = models.ForeignKey("parties.CaseParty", null=True, blank=True, on_delete=models.SET_NULL, related_name="exhibits",
                              verbose_name="ตรวจยึดจาก (บุคคลในคดี)")  # fmt: skip
    is_digital = models.BooleanField("เป็นพยานหลักฐานดิจิทัล", default=False, help_text="โทรศัพท์ คอมพิวเตอร์ ไฟล์ บัญชีออนไลน์")
    holder = models.CharField("ผู้ครอบครองปัจจุบัน", max_length=200, blank=True)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ของกลาง"
        verbose_name_plural = "ของกลาง"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.order}. {self.name}"


class EvidenceFile(models.Model):
    """ไฟล์พยานหลักฐาน: เขียนครั้งเดียว · SHA-256 ตอนรับ · ไฟล์บนดิสก์อ่านอย่างเดียว · ห้ามแก้ต้นฉบับ (B6)."""

    class Kind(models.TextChoices):
        IMAGE = "image", "ภาพ"
        PDF = "pdf", "PDF"
        AUDIO = "audio", "เสียง"
        VIDEO = "video", "วิดีโอ"
        DOCUMENT = "document", "เอกสาร"
        OTHER = "other", "อื่น ๆ"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="evidence_files", verbose_name="คดี")
    exhibit = models.ForeignKey(
        Exhibit, null=True, blank=True, on_delete=models.SET_NULL, related_name="files", verbose_name="เป็นส่วนของของกลาง"
    )
    title = models.CharField("ชื่อพยานหลักฐาน", max_length=300)
    kind = models.CharField("ชนิด", max_length=10, choices=Kind.choices)
    file = models.FileField("ไฟล์", upload_to=evidence_path, max_length=300)
    original_name = models.CharField("ชื่อไฟล์เดิม", max_length=300)
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField("ขนาด (ไบต์)")
    sha256 = models.CharField("SHA-256", max_length=64, db_index=True)
    source = models.CharField("ได้มาจาก", max_length=300, blank=True, help_text="เช่น ผู้เสียหายส่งทางไลน์, คัดลอกจากโทรศัพท์ของกลาง")
    collected_at = models.DateTimeField("วันเวลาที่ได้มา", null=True, blank=True)
    is_digital = models.BooleanField("พยานหลักฐานดิจิทัล (ลงบันทึกตรวจยึด)", default=True)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    origin = models.CharField("ที่มาในระบบ", max_length=30, default="upload", help_text="upload | interview")
    withdrawn_at = models.DateTimeField("ถอนออกจากสำนวนเมื่อ", null=True, blank=True)
    withdrawn_reason = models.CharField("เหตุผลที่ถอน", max_length=300, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ผู้รับเข้า")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ไฟล์พยานหลักฐาน"
        verbose_name_plural = "ไฟล์พยานหลักฐาน"
        ordering = ["uploaded_at", "id"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.pk:  # write once: the file and its hash never change after it is received
            old = EvidenceFile.objects.filter(pk=self.pk).values("file", "sha256", "size").first()
            if old and (old["file"] != self.file.name or old["sha256"] != self.sha256 or old["size"] != self.size):
                raise ValidationError("ไฟล์พยานหลักฐานแก้ไขไม่ได้ (เขียนครั้งเดียว)")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("ลบไฟล์พยานหลักฐานไม่ได้ ใช้ “ถอนออกจากสำนวน” พร้อมเหตุผลแทน")


class CustodyEvent(models.Model):
    """Chain of custody: ทุกการรับเข้า เปิดดู ดาวน์โหลด ส่งมอบ รับคืน และตรวจความครบถ้วน ว่าใคร เมื่อไร เพราะอะไร (B6)."""

    class Action(models.TextChoices):
        RECEIVE = "receive", "รับเข้า"
        VIEW = "view", "เปิดดู"
        DOWNLOAD = "download", "ดาวน์โหลด"
        HANDOVER = "handover", "ส่งมอบ"
        RETURN = "return", "รับคืน"
        MOVE = "move", "ย้ายที่เก็บ/เปลี่ยนสถานะ"
        VERIFY = "verify", "ตรวจความครบถ้วน (SHA-256)"
        WITHDRAW = "withdraw", "ถอนออกจากสำนวน"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="custody_events")
    evidence = models.ForeignKey(EvidenceFile, null=True, blank=True, on_delete=models.CASCADE, related_name="events")
    exhibit = models.ForeignKey(Exhibit, null=True, blank=True, on_delete=models.CASCADE, related_name="events")
    action = models.CharField("การกระทำ", max_length=10, choices=Action.choices)
    at = models.DateTimeField("เมื่อ", auto_now_add=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="โดย")
    reason = models.CharField("เหตุผล", max_length=300)
    to_whom = models.CharField("ส่งมอบให้/รับจาก", max_length=200, blank=True)
    ok = models.BooleanField("ผลตรวจ", null=True, blank=True)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)

    class Meta:
        verbose_name = "บันทึกการครอบครองพยานหลักฐาน"
        verbose_name_plural = "บันทึกการครอบครองพยานหลักฐาน"
        ordering = ["-at", "-id"]
