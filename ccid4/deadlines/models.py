from django.conf import settings
from django.db import models, transaction


class DeadlineRule(models.Model):
    """กติกากำหนดเวลา เก็บในฐานข้อมูล ผู้ดูแลแก้ได้ และทุกการแก้เก็บเป็นเวอร์ชัน (A5)."""

    key = models.CharField("รหัสกติกา", max_length=40, unique=True)
    name = models.CharField("ชื่อกติกา", max_length=200)
    params = models.JSONField("ค่าที่ใช้คำนวณ", default=dict)
    active = models.BooleanField("ใช้งาน", default=True)
    version = models.PositiveIntegerField("ฉบับที่", default=1)
    updated_at = models.DateTimeField("แก้ล่าสุด", auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    note = models.CharField("เหตุผลที่แก้", max_length=300, blank=True)

    class Meta:
        verbose_name = "กติกากำหนดเวลา"
        verbose_name_plural = "กติกากำหนดเวลา"
        ordering = ["key"]

    def __str__(self):
        return self.name

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            old = DeadlineRule.objects.select_for_update().get(pk=self.pk)
            if old.params != self.params or old.active != self.active:
                self.version = old.version + 1
        super().save(*args, **kwargs)
        DeadlineRuleVersion.objects.get_or_create(
            rule=self,
            version=self.version,
            defaults={"params": self.params, "active": self.active, "changed_by": self.updated_by, "note": self.note},
        )


class DeadlineRuleVersion(models.Model):
    rule = models.ForeignKey(DeadlineRule, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField("ฉบับที่")
    params = models.JSONField("ค่าที่ใช้คำนวณ")
    active = models.BooleanField(default=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    note = models.CharField("เหตุผลที่แก้", max_length=300, blank=True)

    class Meta:
        verbose_name = "ประวัติกติกา"
        verbose_name_plural = "ประวัติกติกา"
        unique_together = [("rule", "version")]
        ordering = ["rule", "-version"]


class Holiday(models.Model):
    date = models.DateField("วันที่", unique=True)
    name = models.CharField("ชื่อวันหยุด", max_length=200)

    class Meta:
        verbose_name = "วันหยุดราชการ"
        verbose_name_plural = "วันหยุดราชการ"
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} {self.name}"


class Detention(models.Model):
    """ผัดฝากขัง หรือ ผัดฟ้อง ของผู้ต้องหาหนึ่งคน (หนึ่งแถวต่อหนึ่งผัด)."""

    class Kind(models.TextChoices):
        REMAND = "remand", "ฝากขัง"
        DELAY = "delay", "ผัดฟ้อง (ศาลแขวง)"

    party = models.ForeignKey("parties.CaseParty", on_delete=models.CASCADE, related_name="detentions", verbose_name="ผู้ต้องหา")
    kind = models.CharField("ประเภท", max_length=10, choices=Kind.choices, default=Kind.REMAND)
    start_date = models.DateField("ตั้งแต่วันที่")
    end_date = models.DateField("ถึงวันที่")
    court = models.CharField("ศาล", max_length=200, blank=True)
    order_no = models.CharField("เลขคำร้อง/คำสั่ง", max_length=60, blank=True)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ผัดฝากขัง/ผัดฟ้อง"
        verbose_name_plural = "ผัดฝากขัง/ผัดฟ้อง"
        ordering = ["start_date", "id"]

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class Task(models.Model):
    """สิ่งที่ต้องทำ + บันทึกพนักงานสอบสวน: one list. Auto entries from actions are created as done (A4.6)."""

    class Source(models.TextChoices):
        RULE = "rule", "จากกติกา"
        MANUAL = "manual", "เพิ่มเอง"
        AUTO = "auto", "บันทึกอัตโนมัติ"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="tasks", verbose_name="คดี")
    title = models.CharField("รายการ", max_length=300)
    reason = models.CharField("เหตุผล/คำสั่งที่อ้างอิง", max_length=300, blank=True)
    source = models.CharField("ที่มา", max_length=10, choices=Source.choices, default=Source.MANUAL)
    rule_key = models.CharField(max_length=80, blank=True)
    done_at = models.DateTimeField("ทำแล้วเมื่อ", null=True, blank=True)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    show_to_complainant = models.BooleanField("ให้ผู้แจ้งเห็น", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "งาน/บันทึกพนักงานสอบสวน"
        verbose_name_plural = "งาน/บันทึกพนักงานสอบสวน"
        ordering = ["done_at", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["case", "rule_key"], condition=~models.Q(rule_key=""), name="one_task_per_rule")
        ]

    def __str__(self):
        return self.title

    @property
    def is_done(self):
        return self.done_at is not None
