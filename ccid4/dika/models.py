from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from pgvector.django import HnswIndex, VectorField


class DikaRuling(models.Model):
    """คำพิพากษาศาลฎีกา จากชุดข้อมูลที่หน่วยงานมีสิทธิ์ใช้ นำเข้าด้วย manage.py import_dika."""

    number = models.CharField("เลขฎีกา", max_length=40, unique=True)
    year = models.PositiveIntegerField("ปี พ.ศ.", null=True, blank=True)
    sections = models.CharField("มาตรา", max_length=400, blank=True)
    keywords = models.CharField("คำสำคัญ", max_length=400, blank=True)
    summary = models.TextField("ย่อคำวินิจฉัย")
    full_text = models.TextField("ฉบับเต็ม", blank=True)
    source = models.CharField("ชุดข้อมูล", max_length=200, blank=True)
    embedding = VectorField(dimensions=settings.AI_EMBED_DIM, null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "คำพิพากษาศาลฎีกา"
        verbose_name_plural = "คำพิพากษาศาลฎีกา"
        ordering = ["-year", "number"]
        indexes = [
            GinIndex(fields=["summary"], name="dika_summary_trgm", opclasses=["gin_trgm_ops"]),
            HnswIndex(
                name="dika_embedding_hnsw", fields=["embedding"], m=16, ef_construction=64, opclasses=["vector_cosine_ops"]
            ),
        ]

    def __str__(self):
        return f"ฎีกาที่ {self.number}"


class PinnedRuling(models.Model):
    """ฎีกาที่ปักหมุดไว้กับคดี ใช้อ้างใน 'รายงานการสอบสวน'."""

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="pinned_rulings")
    ruling = models.ForeignKey(DikaRuling, on_delete=models.CASCADE, related_name="+")
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    pinned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    pinned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("case", "ruling")]
        ordering = ["pinned_at"]
