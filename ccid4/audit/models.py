from django.conf import settings
from django.db import models

ACTIONS = {
    "login": "เข้าสู่ระบบ", "login_failed": "เข้าสู่ระบบไม่สำเร็จ", "logout": "ออกจากระบบ", "locked": "ล็อกบัญชี",
    "2fa_enabled": "เปิดใช้ 2FA", "2fa_verified": "ยืนยัน 2FA", "2fa_failed": "2FA ไม่สำเร็จ",
    "view": "เปิดดู", "reveal": "ดูข้อมูลเต็ม", "create": "สร้าง", "edit": "แก้ไข", "delete": "ลบ",
    "export": "ส่งออก", "download": "ดาวน์โหลด", "listen": "ฟังเสียง", "confirm": "ยืนยัน", "import": "นำเข้า",
}


class AuditLog(models.Model):
    """Append-only. Each row hashes the previous row; the database refuses UPDATE and DELETE."""

    at = models.DateTimeField("เวลา", db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, verbose_name="ผู้ใช้")
    user_label = models.CharField("ชื่อผู้ใช้ขณะนั้น", max_length=200, blank=True)
    action = models.CharField("การกระทำ", max_length=30, db_index=True)
    object_type = models.CharField("ประเภทข้อมูล", max_length=40, blank=True, db_index=True)
    object_id = models.CharField("รหัสข้อมูล", max_length=64, blank=True, db_index=True)
    detail = models.TextField("รายละเอียด", blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    path = models.CharField("หน้า", max_length=300, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64)

    class Meta:
        verbose_name = "บันทึกการใช้งาน"
        verbose_name_plural = "บันทึกการใช้งาน"
        ordering = ["-id"]

    @property
    def action_label(self):
        return ACTIONS.get(self.action, self.action)
