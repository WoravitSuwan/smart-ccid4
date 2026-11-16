from django.conf import settings
from django.db import models


class AICall(models.Model):
    """ทุกการเรียก AI: งาน โมเดล รุ่น prompt เวลา ผู้ใช้ — ไม่เก็บเนื้อหาที่ส่งหรือที่ได้ (สเปก 4)."""

    task = models.CharField("งาน", max_length=60)
    backend = models.CharField(max_length=20)
    model = models.CharField("โมเดล", max_length=120, blank=True)
    prompt_version = models.CharField("รุ่น prompt", max_length=20)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    duration_ms = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=1)
    ok = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "การเรียก AI"
        verbose_name_plural = "การเรียก AI"
        ordering = ["-started_at"]
