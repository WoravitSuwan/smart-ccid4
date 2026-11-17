"""ข้อมูลจำลองเส้นทางเงิน/บัญชีม้า/คริปโต สำหรับคดีออนไลน์ปลอม (seed_cases) — ห้ามใช้กับฐานข้อมูลจริง.

Some mule accounts, a LINE ID, a scam website and a TRON wallet are shared between cases on purpose, so the case
network (B4) has something to show. Every number is random and fictional.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cases.models import Case
from cyber import money, services
from cyber.forms import BANKS
from cyber.models import BankAccount

TRON_DEMO = "T9yED5xMV5ARV98BexN97aLZ1UUq7eKSxm"  # checksum-valid but made-up (bytes 0x41 + 00..13)


class Command(BaseCommand):
    help = "สร้างธุรกรรม บัญชีม้า บัญชีออนไลน์ และกระเป๋าคริปโตปลอม ให้คดีออนไลน์ตัวอย่าง"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, force=False, **kw):
        if not settings.DEBUG and not settings.DEMO_MODE and not force:
            raise CommandError("ปิดอยู่ในโหมดใช้งานจริง ใช้ --force เฉพาะฐานข้อมูลทดลอง")
        cases = list(Case.objects.alive().filter(case_type="online").select_related("owner").order_by("pk")[:8])
        if not cases:
            raise CommandError("ยังไม่มีคดีออนไลน์ รัน seed_cases ก่อน")
        rnd = random.Random(9)
        acc = lambda: f"{rnd.randint(100, 999)}-{rnd.randint(0, 9)}-{rnd.randint(10000, 99999)}-{rnd.randint(0, 9)}"  # noqa: E731
        shared_mule = (acc(), "กรุงไทย", "นายสมมติ บัญชีม้า")
        shared_line, shared_site = "@demo.invest.th", "https://demo-invest-profit.example/register"
        for i, case in enumerate(cases):
            user = case.owner
            victim_no, victim_bank = acc(), rnd.choice(BANKS)
            first = [(acc(), rnd.choice(BANKS), "บัญชีสมมติ ชั้นหนึ่ง") for _ in range(rnd.randint(1, 3))]
            if i % 3 == 0:
                first.append(shared_mule)
            when = case.reported_at - timedelta(days=rnd.randint(1, 10))
            for no, bank, name in first:
                for _ in range(rnd.randint(1, 3)):
                    when += timedelta(minutes=rnd.randint(5, 180))
                    money.add_transfer(case, user, from_no=victim_no, from_bank=victim_bank, from_name="ผู้เสียหาย (สมมติ)", to_no=no, to_bank=bank,
                                       to_name=name, amount=Decimal(rnd.choice([1500, 5000, 12000, 25000, 49999])), occurred_at=when,
                                       channel=rnd.choice(["โมบายแบงก์กิ้ง", "พร้อมเพย์"]), reference=f"DEMO{rnd.randint(10**6, 10**7)}")  # fmt: skip
            no, bank, _ = first[0]
            money.add_transfer(case, user, from_no=no, from_bank=bank, from_victim=False, to_no=acc(), to_bank=rnd.choice(BANKS),
                               to_name="บัญชีสมมติ ชั้นสอง", amount=Decimal(rnd.choice([4000, 9000, 20000])),
                               occurred_at=when + timedelta(minutes=20), channel="โมบายแบงก์กิ้ง")  # fmt: skip
            statuses = list(BankAccount.Status.values)
            for k, a in enumerate(case.bank_accounts.filter(is_victim=False)):
                a.status = statuses[(i + k) % len(statuses)]
                a.save(update_fields=["status"])
            if i % 2 == 0:
                services.add_online(case, user, "line", shared_line, "ที่ปรึกษาการลงทุน (สมมติ)")
            if i % 4 == 1:
                services.add_online(case, user, "url", shared_site, "เว็บลงทุนปลอม (สมมติ)")
            if i in (1, 5):
                services.add_wallet(case, user, "tron", TRON_DEMO, "กระเป๋าที่คนร้ายให้โอน USDT (สมมติ)")
        self.stdout.write(self.style.SUCCESS(f"สร้างเส้นทางเงินจำลองให้ {len(cases)} คดีออนไลน์"))
