"""สร้างบัญชีพนักงานสอบสวนสำหรับเครื่องที่ติดตั้งใหม่ (ถามทีละช่อง).

    python manage.py create_officer
    python manage.py create_officer --username inv1 --rank "ร.ต.อ." --first-name สมมติ --last-name ทดสอบ --position "รอง สว.(สอบสวน)"
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Role, User


class Command(BaseCommand):
    help = "สร้างบัญชีพนักงานสอบสวน (ใช้ระบบได้ทันที ไม่ใช่ผู้ดูแลระบบ)"

    def add_arguments(self, parser):
        for name in ("username", "rank", "first-name", "last-name", "position", "password"):
            parser.add_argument(f"--{name}", default=None)
        parser.add_argument("--role", default=Role.INVESTIGATOR, choices=Role.values)

    def ask(self, opts, key, label, required=True):
        value = opts.get(key.replace("-", "_"))
        while not value:
            value = input(f"{label}: ").strip()
            if not required:
                break
        return value or ""

    def handle(self, *args, **opts):
        username = self.ask(opts, "username", "ชื่อผู้ใช้ (ภาษาอังกฤษ เช่น inv1)")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"มีชื่อผู้ใช้ {username} แล้ว")
        rank = self.ask(opts, "rank", "ยศ (เช่น ร.ต.อ.)", required=False)
        first = self.ask(opts, "first-name", "ชื่อ")
        last = self.ask(opts, "last-name", "นามสกุล")
        position = self.ask(opts, "position", "ตำแหน่ง (เช่น รอง สว.(สอบสวน))", required=False)
        password = opts.get("password")
        while not password:
            p1, p2 = getpass.getpass("รหัสผ่าน (อย่างน้อย 12 ตัว): "), getpass.getpass("พิมพ์ซ้ำ: ")
            if p1 != p2 or len(p1) < 12:
                self.stderr.write("รหัสผ่านไม่ตรงกันหรือสั้นเกินไป")
                continue
            password = p1
        user = User(username=username, rank=rank, first_name=first, last_name=last, position=position, role=opts["role"])
        user.set_password(password)
        user.save()
        self.stdout.write(
            self.style.SUCCESS(f"สร้างบัญชี {user.display_name} ({user.get_role_display()}) แล้ว เข้าสู่ระบบด้วย {username}")
        )
