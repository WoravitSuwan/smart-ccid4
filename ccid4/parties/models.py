from django.conf import settings
from django.db import models

from core import thai


class Person(models.Model):
    title = models.CharField("คำนำหน้า", max_length=40, blank=True)
    first_name = models.CharField("ชื่อ", max_length=120)
    last_name = models.CharField("นามสกุล", max_length=120, blank=True)
    citizen_id = models.CharField("เลขประจำตัวประชาชน", max_length=20, blank=True, db_index=True)
    passport_no = models.CharField("เลขหนังสือเดินทาง", max_length=40, blank=True)
    birth_date = models.DateField("วันเกิด", null=True, blank=True)
    gender = models.CharField("เพศ", max_length=20, blank=True)
    nationality = models.CharField("สัญชาติ", max_length=60, default="ไทย", blank=True)
    ethnicity = models.CharField("เชื้อชาติ", max_length=60, blank=True)
    religion = models.CharField("ศาสนา", max_length=60, blank=True)
    occupation = models.CharField("อาชีพ", max_length=120, blank=True)
    education = models.CharField("การศึกษา", max_length=120, blank=True)
    phone = models.CharField("โทรศัพท์", max_length=40, blank=True, db_index=True)
    house_no = models.CharField("บ้านเลขที่", max_length=40, blank=True)
    moo = models.CharField("หมู่", max_length=20, blank=True)
    building = models.CharField("อาคาร/หมู่บ้าน", max_length=120, blank=True)
    soi = models.CharField("ซอย", max_length=120, blank=True)
    road = models.CharField("ถนน", max_length=120, blank=True)
    tambon = models.CharField("ตำบล/แขวง", max_length=120, blank=True)
    amphoe = models.CharField("อำเภอ/เขต", max_length=120, blank=True)
    province = models.CharField("จังหวัด", max_length=120, blank=True)
    postcode = models.CharField("รหัสไปรษณีย์", max_length=10, blank=True)
    address_text = models.CharField("ที่อยู่ (ตามที่แจ้ง)", max_length=400, blank=True, help_text="ใช้เมื่อยังไม่ได้แยกเป็นบ้านเลขที่ ตำบล อำเภอ")
    contact_name = models.CharField("บุคคลที่ติดต่อได้", max_length=200, blank=True)
    contact_relation = models.CharField("ความเกี่ยวข้อง", max_length=60, blank=True)
    contact_phone = models.CharField("โทรศัพท์ผู้ติดต่อ", max_length=40, blank=True)
    marks = models.TextField("ตำหนิรูปพรรณ", blank=True)

    class Meta:
        verbose_name = "บุคคล"
        verbose_name_plural = "บุคคล"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.title}{self.first_name} {self.last_name}".strip()

    @property
    def address(self):
        parts = [
            self.house_no,
            f"ม.{self.moo}" if self.moo else "",
            self.building,
            f"ซ.{self.soi}" if self.soi else "",
            f"ถ.{self.road}" if self.road else "",
            f"ต.{self.tambon}" if self.tambon else "",
            f"อ.{self.amphoe}" if self.amphoe else "",
            f"จ.{self.province}" if self.province else "",
            self.postcode,
        ]
        return " ".join(p for p in parts if p) or self.address_text

    @property
    def masked_id(self):
        return thai.mask_citizen_id(self.citizen_id)


class PartyRole(models.TextChoices):
    ACCUSER = "accuser", "ผู้กล่าวหา"
    SUSPECT = "suspect", "ผู้ต้องหา"
    WITNESS = "witness", "พยาน"


class CaseParty(models.Model):
    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="parties", verbose_name="คดี")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="roles", verbose_name="บุคคล")
    role = models.CharField("บทบาท", max_length=20, choices=PartyRole.choices)
    order = models.PositiveSmallIntegerField("ลำดับ", default=1)
    # Suspect-specific (A4.4)
    arrested_at = models.DateTimeField("วันเวลาถูกจับ/มอบตัว", null=True, blank=True)
    arrived_at = models.DateTimeField("วันเวลาถึงที่ทำการพนักงานสอบสวน", null=True, blank=True)
    custody_place = models.CharField("สถานที่ควบคุม", max_length=200, blank=True)
    bail_status = models.CharField("การประกันตัว", max_length=200, blank=True)
    rights_informed_at = models.DateTimeField("แจ้งสิทธิเมื่อ", null=True, blank=True)
    plea = models.CharField(
        "แนวคำให้การ", max_length=20, blank=True, choices=[("confess", "รับสารภาพ"), ("deny", "ปฏิเสธ"), ("later", "ให้การในชั้นศาล")]
    )

    class Meta:
        verbose_name = "คู่กรณีในคดี"
        ordering = ["role", "order", "id"]

    def __str__(self):
        return f"{self.get_role_display()} {self.order}: {self.person}"

    @property
    def current_statement(self):
        return self.statements.order_by("-version").first()


class PropertyItem(models.Model):
    """บัญชีทรัพย์ถูกประทุษร้าย ของผู้กล่าวหา (A4.3)."""

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="properties", verbose_name="คดี")
    name = models.CharField("ชื่อทรัพย์", max_length=300)
    quantity = models.CharField("จำนวน", max_length=60, default="1")
    value = models.DecimalField("มูลค่า (บาท)", max_digits=14, decimal_places=2, default=0)
    status = models.CharField(
        "สถานะ", max_length=20, choices=[("not_returned", "ไม่ได้คืน"), ("returned", "ได้คืน")], default="not_returned"
    )
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)

    class Meta:
        verbose_name = "ทรัพย์ถูกประทุษร้าย"
        ordering = ["id"]


class Statement(models.Model):
    """คำให้การ: every save is a new version; confirmation records who and when."""

    class Source(models.TextChoices):
        MANUAL = "manual", "พิมพ์เอง"
        AI = "ai", "ร่างโดย AI"
        VOICE = "voice", "จากเสียงสอบปากคำ"

    party = models.ForeignKey(CaseParty, on_delete=models.CASCADE, related_name="statements", verbose_name="บุคคล")
    version = models.PositiveIntegerField("ฉบับที่")
    text = models.TextField("คำให้การ")
    source = models.CharField("ที่มา", max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "คำให้การ"
        ordering = ["-version"]
        unique_together = [("party", "version")]

    @property
    def needs_review(self):
        return self.source != self.Source.MANUAL and not self.confirmed_at
