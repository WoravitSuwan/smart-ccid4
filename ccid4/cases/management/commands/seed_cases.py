"""30 fictional cases across every type and status (Faker th_TH). Never use on a real database."""

import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from faker import Faker

from accounts.models import User
from cases.models import Case, CaseStatus, CaseType
from parties.models import CaseParty, Person

OFFENSES = {
    "online": [
        ("ฉ้อโกงประชาชนผ่านระบบคอมพิวเตอร์ (หลอกลงทุน)", "ป.อ. ม.343, พ.ร.บ.คอมพิวเตอร์ฯ ม.14(1)", 60),
        ("ฉ้อโกงซื้อขายสินค้าออนไลน์", "ป.อ. ม.341", 36),
        ("ยินยอมให้ผู้อื่นใช้บัญชีเงินฝาก", "[อ้างอิงกฎหมาย]", 36),
        ("แอบอ้างเป็นเจ้าหน้าที่รัฐหลอกให้โอนเงิน", "ป.อ. ม.342, ม.343", 84),
    ],
    "criminal": [
        ("ลักทรัพย์", "ป.อ. ม.334", 36),
        ("ทำร้ายร่างกาย", "ป.อ. ม.295", 24),
        ("บุกรุก", "ป.อ. ม.362", 12),
        ("ปลอมเอกสาร", "ป.อ. ม.264", 36),
    ],
    "traffic": [("ขับรถโดยประมาทเป็นเหตุให้ผู้อื่นได้รับอันตราย", "ป.อ. ม.300", 36), ("เมาแล้วขับ", "พ.ร.บ.จราจรทางบกฯ", 12)],
}


class Command(BaseCommand):
    help = "สร้างคดีปลอม (ค่าเริ่มต้น 30 คดี)"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=30)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, count, force, **kw):
        if not settings.DEBUG and not settings.DEMO_MODE and not force:
            raise CommandError("ปิดอยู่ในโหมดใช้งานจริง ใช้ --force เฉพาะฐานข้อมูลทดลอง")
        owners = list(User.objects.filter(role="investigator"))
        if not owners:
            raise CommandError("ยังไม่มีพนักงานสอบสวน รัน seed_demo ก่อน")
        fake = Faker("th_TH")
        Faker.seed(4)
        rnd = random.Random(4)
        types = [t for t in CaseType.values for _ in range({"online": 6, "criminal": 3, "traffic": 1}[t])]
        statuses = CaseStatus.values
        now = timezone.now()
        for i in range(count):
            ctype = types[i % len(types)]
            offense, law, months = rnd.choice(OFFENSES[ctype])
            reported = now - timedelta(days=rnd.randint(0, 200), hours=rnd.randint(0, 23))
            status = statuses[i % len(statuses)]
            case = Case.objects.create(
                case_type=ctype,
                status=status,
                case_no=f"{100 + i}/2569" if status != "received" else "",
                receipt_no=f"L{rnd.randint(10**10, 10**11 - 1)}" if ctype == "online" else "",
                offense=offense,
                law_sections=law,
                max_penalty_months=months,
                court_type="provincial" if months > 36 else "magistrate",
                reported_at=reported,
                incident_from=reported - timedelta(days=rnd.randint(1, 30)),
                location=f"{fake.street_address()} {fake.province()}",
                circumstances=fake.paragraph(nb_sentences=3),
                damage_amount=Decimal(rnd.choice([0, 3500, 25000, 148500, 486500, 1250000])),
                owner=rnd.choice(owners),
                title=offense,
            )
            for role, n in (("accuser", 1), ("suspect", rnd.randint(0, 2)), ("witness", rnd.randint(0, 2))):
                for k in range(n):
                    p = Person.objects.create(
                        title=rnd.choice(["นาย", "นาง", "นางสาว"]),
                        first_name=fake.first_name(),
                        last_name=fake.last_name(),
                        citizen_id=f"{rnd.randint(1, 8)}{rnd.randint(10**11, 10**12 - 1)}",
                        phone=f"08{rnd.randint(10**7, 10**8 - 1)}",
                        house_no=str(rnd.randint(1, 300)),
                        tambon=fake.tambon(),
                        amphoe=fake.amphoe(),
                        province=fake.province(),
                        postcode=fake.postcode(),
                    )
                    CaseParty.objects.create(case=case, person=p, role=role, order=k + 1)
        self.stdout.write(self.style.SUCCESS(f"สร้างคดีปลอม {count} คดี"))
