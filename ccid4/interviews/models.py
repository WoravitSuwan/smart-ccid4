"""ห้องสอบปากคำด้วยเสียง (B2, docs/SPEC_interview_recorder.md)."""

from django.conf import settings
from django.db import models


class InterviewSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "เตรียมการ"
        RECORDING = "recording", "กำลังบันทึก"
        PROCESSING = "processing", "กำลังประมวลผล"
        TRANSCRIPT = "transcript", "รอข้อความถอดเสียง"
        REVIEW = "review", "รอตรวจ"
        CONFIRMED = "confirmed", "ยืนยันแล้ว"

    class Mode(models.TextChoices):
        CLOSE = "close", "ใกล้คำพูด"
        WRITTEN = "written", "ภาษาเขียน"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="interviews", verbose_name="คดี")
    party = models.ForeignKey("parties.CaseParty", on_delete=models.CASCADE, related_name="interviews", verbose_name="ผู้ให้การ")
    place = models.CharField("สถานที่สอบปากคำ", max_length=300)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    mode = models.CharField("ระดับการเรียบเรียง", max_length=10, choices=Mode.choices, default=Mode.CLOSE)
    statement_format = models.CharField(
        "รูปแบบคำให้การ", max_length=10, choices=[("narrative", "บรรยาย (เล่าเรื่อง)"), ("qa", "ถาม–ตอบ")], default="narrative"
    )
    progress = models.JSONField("ขั้นการประมวลผล", default=list, blank=True)  # [{"key","label","state","note"}]
    error = models.CharField(max_length=400, blank=True)
    job = models.CharField("งานประมวลผลรอบล่าสุด", max_length=32, blank=True)  # a newer run supersedes an older one
    rights_override = models.TextField("เหตุผลที่ยืนยันแม้ตรวจไม่พบการแจ้งสิทธิครบ", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    statement = models.ForeignKey("parties.Statement", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "การสอบปากคำด้วยเสียง"
        ordering = ["-created_at"]

    def __str__(self):
        return f"สอบปากคำ {self.party.person} ({self.party.get_role_display()})"

    @property
    def is_suspect(self) -> bool:
        return self.party.role == "suspect"

    @property
    def locked(self) -> bool:
        return self.status == self.Status.CONFIRMED


class Participant(models.Model):
    class Role(models.TextChoices):
        INVESTIGATOR = "investigator", "พนักงานสอบสวน"
        SUBJECT = "subject", "ผู้ให้การ"
        LAWYER = "lawyer", "ทนายความ"
        TRUSTED = "trusted", "ผู้ซึ่งผู้ให้การไว้วางใจ"
        INTERPRETER = "interpreter", "ล่าม"
        RECORDER = "recorder", "ผู้บันทึก"

    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="participants")
    role = models.CharField("บทบาท", max_length=12, choices=Role.choices)
    name = models.CharField("ชื่อ", max_length=200)
    language = models.CharField("ภาษา (ล่าม)", max_length=60, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.get_role_display()} {self.name}"


def chunk_path(instance, filename):
    return f"interviews/{instance.session_id}/chunks/{filename}"


def audio_path(instance, filename):
    return f"interviews/{instance.session_id}/{filename}"


class RecordingChunk(models.Model):
    """ท่อนเสียง 5 วินาทีจากเบราว์เซอร์. take = รอบการอัด (เริ่มใหม่หลังปิดแท็บ/เน็ตหลุด = รอบใหม่ มีหัวไฟล์ของตัวเอง)."""

    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="chunks")
    take = models.PositiveIntegerField(default=1)
    seq = models.PositiveIntegerField()
    file = models.FileField(upload_to=chunk_path)
    size = models.PositiveIntegerField()
    seconds = models.FloatField(default=5.0)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["take", "seq"]
        unique_together = [("session", "take", "seq")]


class Recording(models.Model):
    """ไฟล์เสียงต้นฉบับหลังปิดการบันทึก: อ่านอย่างเดียว พร้อม SHA-256 (หลักฐาน B6)."""

    session = models.OneToOneField(InterviewSession, on_delete=models.CASCADE, related_name="recording")
    original = models.FileField(upload_to=audio_path)
    for_asr = models.FileField(upload_to=audio_path, blank=True)  # FLAC 16 kHz mono
    duration = models.FloatField(default=0)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=60, default="audio/webm")
    closed_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")


class Marker(models.Model):
    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="markers")
    at = models.FloatField("วินาทีที่")
    note = models.CharField("หมายเหตุ", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["at"]


class Segment(models.Model):
    """ช่วงพูดจากการถอดเสียง. text_ai = ข้อความต้นฉบับจาก AI เก็บไว้เสมอ, text = ข้อความปัจจุบันหลังตรวจ."""

    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="segments")
    idx = models.PositiveIntegerField()
    start = models.FloatField()
    end = models.FloatField()
    speaker = models.CharField("ผู้พูด (จากระบบ)", max_length=20, default="S1")
    text_ai = models.TextField("ข้อความถอดเสียงต้นฉบับ")
    text = models.TextField("ข้อความปัจจุบัน")
    confidence = models.FloatField(null=True, blank=True)
    words = models.JSONField(default=list, blank=True)  # [{"w","s","e","p"}]

    class Meta:
        ordering = ["start", "idx"]
        unique_together = [("session", "idx")]


class SegmentEdit(models.Model):
    segment = models.ForeignKey(Segment, on_delete=models.CASCADE, related_name="edits")
    old = models.TextField()
    new = models.TextField()
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


class SpeakerMap(models.Model):
    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="speaker_maps")
    label = models.CharField(max_length=20)
    role = models.CharField(max_length=12, choices=Participant.Role.choices, blank=True)

    class Meta:
        unique_together = [("session", "label")]


class RightsCheck(models.Model):
    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="rights")
    key = models.CharField(max_length=20)
    label = models.CharField(max_length=200)
    found_at = models.FloatField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["id"]
        unique_together = [("session", "key")]


class StatementDraft(models.Model):
    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="drafts")
    version = models.PositiveIntegerField()
    mode = models.CharField(max_length=10, choices=InterviewSession.Mode.choices)
    source = models.CharField(max_length=10, default="code")  # code | ai
    locked = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    uncovered = models.JSONField("ช่วงที่ผู้ให้การพูดแต่ไม่อยู่ในคำตอบ", default=list, blank=True)
    points = models.JSONField("สรุปใจความสำคัญ", default=list, blank=True)
    narrative = models.TextField("เล่าเรื่อง (คำให้การแบบบรรยาย)", blank=True)
    narrative_flags = models.JSONField(default=list, blank=True)
    narrative_reviewed = models.BooleanField(default=False)
    keywords = models.JSONField("คำที่พบบ่อย", default=list, blank=True)  # [[word, count], ...]
    summary_source = models.CharField(max_length=10, default="code")

    class Meta:
        ordering = ["-version"]
        unique_together = [("session", "version")]


class QAPair(models.Model):
    draft = models.ForeignKey(StatementDraft, on_delete=models.CASCADE, related_name="pairs")
    order = models.PositiveIntegerField()
    question = models.TextField("ถาม")
    answer = models.TextField("ตอบ")
    start = models.FloatField()
    end = models.FloatField()
    segment_ids = models.JSONField(default=list)
    flags = models.JSONField(default=list, blank=True)
    reviewed = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]


def doc_path(instance, filename):
    return f"interviews/{instance.session_id}/documents/{filename}"


class InterviewDocument(models.Model):
    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="documents")
    draft = models.ForeignKey(StatementDraft, on_delete=models.PROTECT, related_name="+")
    code = models.CharField("รหัสเอกสาร", max_length=40)
    docx = models.FileField(upload_to=doc_path)
    pdf = models.FileField(upload_to=doc_path, blank=True)
    evidence_zip = models.FileField(upload_to=doc_path, blank=True)
    hashes = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


DEFAULT_SCRIPT = (
    "การสอบปากคำครั้งนี้มีการบันทึกเสียงไว้เป็นหลักฐาน\n"
    "พนักงานสอบสวนขอแจ้งข้อกล่าวหาว่า ท่าน …(ข้อกล่าวหา)…\n"
    "ก่อนถามคำให้การ ขอแจ้งว่าท่านมีสิทธิที่จะให้การหรือไม่ให้การก็ได้ ถ้าให้การ ถ้อยคำที่ให้การนั้นอาจใช้เป็นพยานหลักฐานในการพิจารณาคดีได้\n"
    "และท่านมีสิทธิให้ทนายความหรือผู้ซึ่งท่านไว้วางใจเข้าฟังการสอบปากคำได้"
)


class InterviewSettings(models.Model):
    """ค่าตั้งของห้องสอบ (แถวเดียว): สคริปต์แจ้งสิทธิ (ให้พี่เลี้ยงตรวจถ้อยคำ) และระยะเก็บไฟล์เสียง."""

    rights_script = models.TextField("สคริปต์แจ้งสิทธิผู้ต้องหา", default=DEFAULT_SCRIPT)
    retention_days = models.PositiveIntegerField("ระยะเก็บไฟล์เสียง (วัน)", default=3650)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()
