"""Fictional demo users for every role (Faker th_TH). Refuses to run on a production database."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from faker import Faker

from accounts.models import Role, User

DEMO_PASSWORD = "ccid4-demo-2569"
RANKS = {"investigator": "พ.ต.ท.", "assistant": "ด.ต.", "supervisor": "พ.ต.อ.", "commander": "พล.ต.ต.", "clerk": "ส.ต.อ.", "admin": ""}
POSITIONS = {"investigator": "สารวัตร (สอบสวน)", "assistant": "ผู้ช่วยพนักงานสอบสวน", "supervisor": "ผู้กำกับการ",
             "commander": "ผู้บังคับการ", "clerk": "เจ้าหน้าที่ธุรการ", "admin": "ผู้ดูแลระบบ"}


class Command(BaseCommand):
    help = "สร้างผู้ใช้ตัวอย่างทุกบทบาท (ข้อมูลปลอม)"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ยอมให้รันเมื่อ DEBUG ปิด (ห้ามใช้กับฐานข้อมูลจริง)")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not settings.DEMO_MODE and not opts["force"]:
            raise CommandError("ปิดอยู่ในโหมดใช้งานจริง ใช้ --force เฉพาะฐานข้อมูลทดลอง")
        fake = Faker("th_TH")
        Faker.seed(2569)
        created = []
        for role in Role.values:
            for i in range(2 if role in ("investigator", "assistant") else 1):
                username = f"{role}{i + 1}"
                if User.objects.filter(username=username).exists():
                    continue
                u = User(username=username, role=role, rank=RANKS[role], position=POSITIONS[role],
                         first_name=fake.first_name(), last_name=fake.last_name(), phone="00 0000 0000",
                         unit_group="กก.1")
                u.set_password(DEMO_PASSWORD)
                u.save()
                created.append(username)
        self.stdout.write(self.style.SUCCESS(f"สร้างผู้ใช้ {len(created)} บัญชี รหัสผ่านทุกบัญชี: {DEMO_PASSWORD}"))
        for name in created:
            self.stdout.write(f"  {name}")
