"""สร้างดัชนีตัวระบุข้ามคดี (B4) ใหม่ทั้งหมด — ใช้หลังอัปเกรดหรือเปลี่ยน IDENTIFIER_KEY/DJANGO_SECRET_KEY."""

from django.core.management.base import BaseCommand

from cases.models import Case
from cyber.index import reindex_case
from cyber.models import Identifier


class Command(BaseCommand):
    help = "สร้างดัชนีเลขบัญชี/เบอร์โทร/เลขบัตร/บัญชีโซเชียล/คริปโต ของทุกคดีใหม่"

    def add_arguments(self, parser):
        parser.add_argument("--missing", action="store_true", help="เฉพาะคดีที่ยังไม่มีดัชนี (เร็ว ใช้ทุกครั้งที่เปิดระบบได้)")

    def handle(self, missing=False, **_):
        qs = Case.objects.alive().select_related("owner")
        if missing:
            qs = qs.exclude(pk__in=Identifier.objects.values("case_id"))
        n = 0
        for case in qs.iterator():
            reindex_case(case)
            n += 1
        self.stdout.write(f"สร้างดัชนีตัวระบุแล้ว {n} คดี")
