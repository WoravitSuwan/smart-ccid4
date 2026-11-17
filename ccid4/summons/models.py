"""หมายเรียกจากทะเบียนคดีของกอง (B1, docs/SPEC_summons_system.md)."""

from django.conf import settings
from django.db import models

from core import thai


class RegisterStatus(models.Model):
    """สถานะในทะเบียนของกอง (ต่างจากสถานะคดีของ CCID4). is_final = สิ้นสุดแล้ว ไม่ต้องออกหมาย."""

    name = models.CharField("สถานะ", max_length=100, unique=True)
    is_final = models.BooleanField("ถือว่าสิ้นสุด", default=False)
    auto_created = models.BooleanField("พบครั้งแรกตอนนำเข้า", default=False)

    class Meta:
        verbose_name = "สถานะในทะเบียน"
        verbose_name_plural = "สถานะในทะเบียน"
        ordering = ["name"]

    def __str__(self):
        return self.name


class RegisterUnit(models.Model):
    name = models.CharField("หน่วยที่รับผิดชอบ", max_length=200, unique=True)
    issue_place = models.CharField("สถานที่ออกหมาย", max_length=300, blank=True)
    appointment_place = models.TextField("สถานที่นัด (ไป ณ ที่ …)", blank=True)
    sender_address = models.TextField("ที่อยู่ผู้ส่ง (บนซอง)", blank=True)

    class Meta:
        verbose_name = "หน่วย (ทะเบียนหมายเรียก)"
        ordering = ["name"]

    def __str__(self):
        return self.name


class RegisterInvestigator(models.Model):
    name = models.CharField("ชื่อตามทะเบียน", max_length=200, unique=True)
    rank_full = models.CharField("ยศเต็ม", max_length=100, blank=True, help_text="เช่น พันตำรวจโท")
    sign_name = models.CharField("ชื่อ-สกุลที่ลงนาม", max_length=200, blank=True)
    position = models.CharField("ตำแหน่ง", max_length=200, blank=True)
    phone = models.CharField("โทรศัพท์", max_length=40, blank=True)
    unit = models.ForeignKey(RegisterUnit, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="หน่วย")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                             verbose_name="บัญชีผู้ใช้ใน CCID4")  # fmt: skip

    class Meta:
        verbose_name = "พนักงานสอบสวน (ทะเบียน)"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def ready(self) -> bool:
        return bool(self.rank_full and self.sign_name and self.position)


ADDRESS_FIELDS = ["house_no", "moo", "building", "soi", "road", "tambon", "amphoe", "province"]


class Complainant(models.Model):
    """ผู้แจ้ง (ไฟล์ที่ 1). เก็บตัวเลขเป็นข้อความเสมอ (เลข 0 นำหน้า)."""

    receipt_no = models.CharField("เลขรับแจ้ง", max_length=20, unique=True)
    title = models.CharField("คำนำหน้าชื่อ", max_length=40, blank=True)
    first_name = models.CharField("ชื่อ", max_length=120, blank=True)
    last_name = models.CharField("นามสกุล", max_length=120, blank=True)
    age = models.CharField("อายุ", max_length=10, blank=True)
    citizen_id = models.CharField("เลขประจำตัวประชาชน", max_length=20, blank=True)
    phone = models.CharField("หมายเลขโทรศัพท์", max_length=40, blank=True)
    house_no = models.CharField("บ้านเลขที่", max_length=60, blank=True)
    moo = models.CharField("หมู่", max_length=20, blank=True)
    building = models.CharField("อาคาร/ตึก", max_length=200, blank=True)
    soi = models.CharField("ซอย", max_length=200, blank=True)
    road = models.CharField("ถนน", max_length=200, blank=True)
    tambon = models.CharField("ตำบล", max_length=120, blank=True)
    amphoe = models.CharField("อำเภอ", max_length=120, blank=True)
    province = models.CharField("จังหวัด", max_length=120, blank=True)
    postcode = models.CharField("รหัสไปรษณีย์ (ในไฟล์)", max_length=10, blank=True)
    postcode_filled = models.CharField("รหัสไปรษณีย์ (ระบบเติม)", max_length=10, blank=True)
    case_type_text = models.CharField("ประเภทคดี", max_length=300, blank=True)
    extra = models.JSONField("คอลัมน์อื่นในไฟล์", default=dict, blank=True)
    issues = models.JSONField("ธงปัญหา", default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ผู้แจ้ง (ทะเบียน)"
        ordering = ["receipt_no"]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        """คำนำหน้า + ชื่อ + นามสกุล; ไม่มีคำนำหน้าก็ไม่ใส่ (ห้ามเดา)."""
        return f"{self.title}{self.first_name} {self.last_name}".strip()

    @property
    def effective_postcode(self) -> str:
        return self.postcode or self.postcode_filled

    @property
    def is_bangkok(self) -> bool:
        return self.province in ("กรุงเทพมหานคร", "กรุงเทพฯ", "กทม.", "กรุงเทพ")

    @property
    def address(self) -> str:
        bkk = self.is_bangkok
        parts = [
            self.house_no,
            f"หมู่ {self.moo}" if self.moo else "",
            self.building,
            f"ซอย{self.soi}" if self.soi else "",
            f"ถนน{self.road}" if self.road else "",
            (f"แขวง{self.tambon}" if bkk else f"ตำบล{self.tambon}") if self.tambon else "",
            (f"เขต{self.amphoe}" if bkk else f"อำเภอ{self.amphoe}") if self.amphoe else "",
            (self.province if bkk else f"จังหวัด{self.province}") if self.province else "",
            self.effective_postcode,
        ]
        return " ".join(p for p in parts if p)

    @property
    def address_incomplete(self) -> bool:
        return not self.house_no or not self.province

    @property
    def masked_id(self) -> str:
        return thai.mask_citizen_id(self.citizen_id)


class RegisterCase(models.Model):
    """แถวในทะเบียนคดีของกอง (ไฟล์ที่ 2) เชื่อมกับคดีใน CCID4."""

    receipt_no = models.CharField("เลขรับแจ้ง", max_length=20, unique=True)
    complainant = models.ForeignKey(Complainant, null=True, blank=True, on_delete=models.SET_NULL, related_name="register_cases")
    case = models.OneToOneField("cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="register")
    sheet = models.CharField("ชีตต้นทาง", max_length=100, blank=True)
    seq = models.CharField("ลำดับ", max_length=20, blank=True)
    case_no = models.CharField("เลขคดีอาญา", max_length=40, blank=True)
    report_date = models.DateField("วันที่รับแจ้ง", null=True, blank=True)
    victim_name = models.CharField("ผู้เสียหาย", max_length=300, blank=True)
    gender = models.CharField("เพศ", max_length=20, blank=True)
    age = models.CharField("อายุ", max_length=10, blank=True)
    case_type = models.CharField("ประเภทคดี", max_length=300, blank=True)
    type_l1 = models.CharField("ประเภทคดีระดับ 1", max_length=300, blank=True)
    type_l2 = models.CharField("ประเภทคดีระดับ 2", max_length=300, blank=True)
    type_l3 = models.CharField("ประเภทคดีระดับ 3", max_length=300, blank=True)
    case_group = models.CharField("ประเภทกลุ่มคดี", max_length=300, blank=True)
    detail = models.TextField("รายละเอียด", blank=True)
    damage = models.DecimalField("มูลค่าความเสียหาย", max_digits=16, decimal_places=2, default=0)
    bureau = models.CharField("บช.", max_length=300, blank=True)
    division = models.CharField("บก./ภ.จว.", max_length=300, blank=True)
    unit = models.ForeignKey(RegisterUnit, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="หน่วยที่รับผิดชอบ")
    investigator = models.ForeignKey(
        RegisterInvestigator, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="พนักงานสอบสวน"
    )
    detective = models.CharField("พนักงานสืบสวน", max_length=200, blank=True)
    status = models.ForeignKey(RegisterStatus, null=True, blank=True, on_delete=models.PROTECT, verbose_name="สถานะ")
    victim_phone = models.CharField("เบอร์โทรศัพท์ผู้เสียหาย", max_length=40, blank=True)
    bank_ref = models.CharField("เลขอ้างอิงธนาคาร", max_length=100, blank=True)
    contact_log = models.CharField("บันทึกการติดต่อ", max_length=100, blank=True)
    daily_record = models.CharField("ประจำวัน", max_length=100, blank=True)
    note = models.TextField("หมายเหตุ", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "คดีในทะเบียน"
        verbose_name_plural = "ทะเบียนคดีของกอง"
        ordering = ["-report_date", "receipt_no"]

    def __str__(self):
        return self.receipt_no

    @property
    def is_open(self) -> bool:
        return not (self.status and self.status.is_final)

    @property
    def summons_count(self) -> int:
        return self.summons.exclude(result=Summons.Result.CANCELLED).count()


class ImportBatch(models.Model):
    class Kind(models.TextChoices):
        COMPLAINANTS = "complainants", "ข้อมูลผู้แจ้ง"
        REGISTER = "register", "ทะเบียนคดีของกอง"

    class Status(models.TextChoices):
        PREVIEW = "preview", "รอยืนยัน"
        DONE = "done", "นำเข้าแล้ว"
        CANCELLED = "cancelled", "ยกเลิก"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to="summons/imports/%Y/%m/")
    file_name = models.CharField(max_length=255)
    sheets = models.JSONField("ชีตที่เลือก", default=list)
    mapping = models.JSONField("การจับคู่คอลัมน์", default=dict)
    counts = models.JSONField("สรุป", default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ImportIssue(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="issues")
    sheet = models.CharField(max_length=100, blank=True)
    row_no = models.PositiveIntegerField()
    receipt_no = models.CharField(max_length=20, blank=True)
    problem = models.CharField(max_length=200)

    class Meta:
        ordering = ["sheet", "row_no"]


class ColumnMapping(models.Model):
    """การจับคู่คอลัมน์ที่ผู้ใช้ตั้งเอง ใช้ครั้งต่อไป."""

    kind = models.CharField(max_length=20, unique=True)
    mapping = models.JSONField(default=dict)  # header in file -> field name
    updated_at = models.DateTimeField(auto_now=True)


def template_path(instance, filename):
    return f"summons/templates/{instance.kind}-v{instance.version}-{filename}"


class SummonsTemplate(models.Model):
    class Kind(models.TextChoices):
        SUMMONS = "summons", "หมายเรียก"
        ENVELOPE = "envelope", "ใบจ่าหน้าซอง"

    kind = models.CharField("ประเภท", max_length=10, choices=Kind.choices)
    name = models.CharField("ชื่อ", max_length=200)
    version = models.PositiveIntegerField("ฉบับที่", default=1)
    file = models.FileField(upload_to=template_path)
    placeholders = models.JSONField("ตัวแทนที่พบ", default=list)
    active = models.BooleanField("ใช้งานอยู่", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "-version"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.name} (ฉบับที่ {self.version})"


class SummonsBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "รอคิว"
        RUNNING = "running", "กำลังสร้าง"
        DONE = "done", "เสร็จ"
        FAILED = "failed", "ไม่สำเร็จ"

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    options = models.JSONField(default=dict)
    total = models.PositiveIntegerField(default=0)
    done_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    bundle = models.FileField(upload_to="summons/batches/%Y/%m/", blank=True)
    combined_pdf = models.FileField(upload_to="summons/batches/%Y/%m/", blank=True)
    combined_docx = models.FileField(upload_to="summons/batches/%Y/%m/", blank=True)
    envelopes = models.FileField(upload_to="summons/batches/%Y/%m/", blank=True)
    error = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def percent(self) -> int:
        return int(100 * self.done_count / self.total) if self.total else 0


def summons_path(instance, filename):
    return f"summons/issued/{instance.register_case.receipt_no}/{filename}"


class Summons(models.Model):
    class Result(models.TextChoices):
        ISSUED = "issued", "ออกแล้ว"
        ATTENDED = "attended", "มาตามนัด"
        ABSENT = "absent", "ไม่มาตามนัด"
        CANCELLED = "cancelled", "ยกเลิก"

    register_case = models.ForeignKey(RegisterCase, on_delete=models.CASCADE, related_name="summons", verbose_name="คดี")
    batch = models.ForeignKey(SummonsBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="summons")
    number = models.PositiveIntegerField("ครั้งที่")
    issued_on = models.DateField("วันออกหมาย")
    appointment_at = models.DateTimeField("วันเวลานัด")
    place = models.TextField("สถานที่นัด")
    reason = models.CharField("เหตุที่เรียก", max_length=400)
    issuer = models.ForeignKey(
        RegisterInvestigator, null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="ผู้ออกหมาย"
    )
    issuer_snapshot = models.JSONField("ข้อมูลผู้ออกหมายขณะออก", default=dict)
    template = models.ForeignKey(SummonsTemplate, null=True, on_delete=models.SET_NULL, related_name="+")
    docx = models.FileField(upload_to=summons_path, blank=True)
    pdf = models.FileField(upload_to=summons_path, blank=True)
    envelope = models.FileField(upload_to=summons_path, blank=True)
    result = models.CharField("ผล", max_length=10, choices=Result.choices, default=Result.ISSUED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "หมายเรียก"
        verbose_name_plural = "หมายเรียก"
        ordering = ["register_case", "number"]
        unique_together = [("register_case", "number")]

    def __str__(self):
        return f"หมายเรียก {self.register_case.receipt_no} ครั้งที่ {self.number}"
