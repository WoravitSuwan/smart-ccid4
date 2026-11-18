from django.conf import settings
from django.db import models


class DeadlineAlert(models.Model):
    """One row per (deadline, stage) already announced, so the hourly check never repeats an alert (B8)."""

    class Stage(models.TextChoices):
        D3 = "d3", "เหลือ 3 วัน"
        D1 = "d1", "เหลือ 1 วัน"
        D0 = "d0", "ครบกำหนดวันนี้"
        OVER = "over", "เลยกำหนด"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="deadline_alerts")
    key = models.CharField(max_length=120)  # rule key + person + due date: a new remand round is a new deadline
    stage = models.CharField(max_length=5, choices=Stage.choices)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["case", "key", "stage"], name="dashboard_alert_once")]


class DigestSetting(models.Model):
    """สรุปรายวันทางอีเมล (ผ่าน SMTP ภายในหน่วย) — ผู้ใช้เลือกเปิดเอง."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="digest")
    enabled = models.BooleanField("รับสรุปรายวันทางอีเมล", default=False)
    email = models.EmailField("อีเมลในระบบของหน่วย", blank=True)
    last_sent = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "สรุปรายวันทางอีเมล"
        verbose_name_plural = "สรุปรายวันทางอีเมล"
