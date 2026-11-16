from django.conf import settings
from django.db import models
from django.utils import timezone


class CaseType(models.TextChoices):
    CRIMINAL = "criminal", "คดีอาญา"
    ONLINE = "online", "คดีออนไลน์ (ไซเบอร์)"
    TRAFFIC = "traffic", "คดีจราจร"


class CaseStatus(models.TextChoices):
    IMPORTED = "imported", "นำเข้า"
    RECEIVED = "received", "รับแจ้งใหม่"
    INVESTIGATING = "investigating", "กำลังสอบสวน"
    CLOSED = "closed", "เสร็จสิ้น"


class CourtType(models.TextChoices):
    MAGISTRATE = "magistrate", "ศาลแขวง"
    PROVINCIAL = "provincial", "ศาลจังหวัด"
    CRIMINAL = "criminal", "ศาลอาญา"
    JUVENILE = "juvenile", "ศาลเยาวชนและครอบครัว"
    CORRUPTION = "corruption", "ศาลอาญาคดีทุจริตฯ"


class CaseQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def visible_to(self, user):
        qs = self.alive()
        if user.can("case.view_all_readonly"):
            return qs
        if user.can("case.view_group") and user.unit_group:
            return qs.filter(
                models.Q(owner__unit_group=user.unit_group) | models.Q(owner=user) | models.Q(shared_with=user)
            ).distinct()
        if user.role in ("investigator", "assistant"):
            return qs.filter(models.Q(owner=user) | models.Q(shared_with=user)).distinct()
        if user.role == "clerk":
            return qs
        return qs.none()


class Case(models.Model):
    case_type = models.CharField("ประเภทคดี", max_length=20, choices=CaseType.choices, default=CaseType.CRIMINAL)
    status = models.CharField("สถานะ", max_length=20, choices=CaseStatus.choices, default=CaseStatus.RECEIVED, db_index=True)
    case_no = models.CharField("เลขคดีอาญา", max_length=40, blank=True, db_index=True)
    receipt_no = models.CharField("เลขรับแจ้ง", max_length=40, blank=True, db_index=True)
    title = models.CharField("เรื่อง", max_length=300, blank=True)

    # บันทึกประจำวัน (A4.2)
    dr_complaint_item = models.CharField("ปจว.รับคำร้องทุกข์ ข้อ", max_length=20, blank=True)
    dr_complaint_no = models.CharField("ปจว.รับคำร้องทุกข์ เลขที่", max_length=40, blank=True)
    dr_complaint_at = models.DateTimeField("ปจว.รับคำร้องทุกข์ วันเวลา", null=True, blank=True)
    dr_caseno_item = models.CharField("ปจว.ตัดเลขคดี ข้อ", max_length=20, blank=True)
    dr_caseno_at = models.DateTimeField("ปจว.ตัดเลขคดี วันเวลา", null=True, blank=True)

    offense = models.CharField("ฐานความผิด", max_length=400, blank=True)
    law_sections = models.CharField("กฎหมายและมาตรา", max_length=400, blank=True)
    penalty_text = models.CharField("อัตราโทษ", max_length=400, blank=True)
    max_penalty_months = models.PositiveIntegerField("โทษจำคุกสูงสุด (เดือน)", null=True, blank=True)
    max_fine = models.PositiveIntegerField("โทษปรับสูงสุด (บาท)", null=True, blank=True)
    court_type = models.CharField("ประเภทศาล", max_length=20, choices=CourtType.choices, blank=True)
    jurisdiction = models.CharField("เขตอำนาจศาล", max_length=200, blank=True)

    incident_from = models.DateTimeField("เกิดเหตุตั้งแต่", null=True, blank=True)
    incident_to = models.DateTimeField("เกิดเหตุถึง", null=True, blank=True)
    location = models.CharField("สถานที่เกิดเหตุ", max_length=400, blank=True)
    location_lat = models.DecimalField("ละติจูด", max_digits=9, decimal_places=6, null=True, blank=True)
    location_lng = models.DecimalField("ลองจิจูด", max_digits=9, decimal_places=6, null=True, blank=True)
    reported_at = models.DateTimeField("วันเวลารับแจ้ง", default=timezone.now)
    circumstances = models.TextField("พฤติการณ์แห่งคดี", blank=True)
    damage_amount = models.DecimalField("มูลค่าความเสียหาย (บาท)", max_digits=14, decimal_places=2, default=0)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_cases", verbose_name="พนักงานสอบสวน"
    )
    shared_with = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="shared_cases", verbose_name="แชร์ให้")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField("อัปเดตล่าสุด", auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    objects = CaseQuerySet.as_manager()

    class Meta:
        verbose_name = "คดี"
        verbose_name_plural = "คดี"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.case_no or self.receipt_no or f"คดี #{self.pk}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("cases:detail", args=[self.pk])

    @property
    def label(self):
        return str(self)

    @property
    def accuser(self):
        p = next((p for p in self.parties.all() if p.role == "accuser"), None)
        return p.person if p else None

    @property
    def nearest_deadline(self):
        return None  # filled in by the deadlines app (phase 3)
