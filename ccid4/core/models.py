from django.conf import settings
from django.db import models


class Notification(models.Model):
    """กระดิ่งในระบบ (B8): ตัวระบุซ้ำข้ามคดี (B4), คำขอรวมสำนวน และงานอื่นที่ต้องรู้."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications", verbose_name="ผู้รับ"
    )
    kind = models.CharField("ประเภท", max_length=30)
    title = models.CharField("หัวข้อ", max_length=200)
    body = models.CharField("รายละเอียด", max_length=500, blank=True)
    url = models.CharField("ลิงก์", max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField("อ่านเมื่อ", null=True, blank=True)

    class Meta:
        verbose_name = "การแจ้งเตือน"
        verbose_name_plural = "การแจ้งเตือน"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return self.title
