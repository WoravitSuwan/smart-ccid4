"""สร้างบัญชีพนักงานสอบสวนสำหรับเครื่องที่ติดตั้งใหม่ (ถามทีละช่อง).

    python manage.py create_officer
    python manage.py create_officer --username inv1 --rank "ร.ต.อ." --first-name สมมติ --last-name ทดสอบ --position "รอง สว.(สอบสวน)"

พิมพ์ภาษาไทยผ่าน `docker exec` มีสองกรณีที่ทำให้ข้อความเสีย (Python ได้ surrogate ซึ่งบันทึกลงฐานข้อมูลไม่ได้)
1. กด Backspace ลบอักษรไทย — เทอร์มินัลในคอนเทนเนอร์ไม่ได้เปิดโหมด UTF-8 (IUTF8) จึงลบแค่ 1 ใน 3 ไบต์ของอักษรไทย
   แก้ที่ต้นเหตุด้วยการเปิด IUTF8 ก่อนถาม และถ้ายังมีเศษไบต์ค้างก็ตัดทิ้ง (ได้ข้อความตามที่เห็นบนจอ)
2. คอนโซล Windows ที่ส่งตัวอักษรเป็น code page 874 (TIS-620) แทน UTF-8 — แปลงกลับให้
ส่วนชื่อผู้ใช้และรหัสผ่านต้องเป็นตัวอักษรภาษาอังกฤษ (หน้าเข้าสู่ระบบพิมพ์ได้ตรงกันแน่นอน)
"""

import contextlib
import getpass
import os
import re
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import DataError

from accounts.models import Role, User

MIN_PASSWORD = 12
USERNAME_RE = re.compile(r"^[A-Za-z0-9@.+_-]{1,150}$")
SURROGATES = re.compile("[\udc80-\udcff]")
FALLBACK_HINT = "ถ้าพิมพ์ภาษาไทยในหน้าต่างนี้ไม่ได้ ให้พิมพ์ภาษาอังกฤษไปก่อน แล้วแก้ที่เมนู ข้อมูลพื้นฐานของฉัน หลังเข้าสู่ระบบ"


def repair_console_text(value: str) -> str:
    """แก้ข้อความที่มาถึงเป็น surrogate: UTF-8 ที่มีเศษไบต์จาก Backspace หรือภาษาไทย code page 874."""
    if not value or not SURROGATES.search(value):
        return value
    raw = value.encode("utf-8", "surrogateescape")
    non_ascii = sum(b >= 0x80 for b in raw)
    utf8 = raw.decode("utf-8", "ignore")
    others = [ch for ch in utf8 if not ch.isascii()]
    thai_bytes = 3 * len(others)  # every Thai letter is 3 bytes in UTF-8
    # UTF-8 Thai with a few stray bytes (Backspace erased only one byte of a letter): keep what was on screen.
    # Thai typed in code page 874 decodes to Cyrillic/Armenian or nothing, so it fails this test.
    if others and all("\u0e00" <= ch <= "\u0e7f" for ch in others) and thai_bytes * 2 >= non_ascii:
        return utf8
    return raw.decode("cp874", "replace")


@contextlib.contextmanager
def utf8_line_editing():
    """เปิด IUTF8 ให้ Backspace ลบอักษรไทยทีละตัวอักษร ไม่ใช่ทีละไบต์ (คืนค่าเดิมเมื่อเสร็จ)."""
    try:
        import termios
    except ImportError:  # Windows without a container
        yield
        return
    restore, opened = [], []
    for source in ("stdin", "/dev/tty"):  # input() reads stdin, getpass() reads /dev/tty
        try:
            if source == "stdin":
                fd = sys.stdin.fileno()
            else:
                fd = os.open(source, os.O_RDWR | os.O_NOCTTY)
                opened.append(fd)
            attrs = termios.tcgetattr(fd)
        except (OSError, ValueError, AttributeError, termios.error):
            continue
        if not attrs[0] & termios.IUTF8:
            changed = list(attrs)
            changed[0] |= termios.IUTF8
            with contextlib.suppress(termios.error):
                termios.tcsetattr(fd, termios.TCSANOW, changed)
                restore.append((fd, attrs))
    try:
        yield
    finally:
        for fd, attrs in reversed(restore):
            with contextlib.suppress(termios.error, OSError):
                termios.tcsetattr(fd, termios.TCSANOW, attrs)
        for fd in opened:
            os.close(fd)


def text_problem(value: str) -> str | None:
    if SURROGATES.search(value) or "�" in value:
        return "อ่านตัวอักษรที่พิมพ์ไม่ได้"
    if "\x00" in value or any(ord(ch) < 32 for ch in value):
        return "มีตัวอักษรพิเศษที่บันทึกไม่ได้"
    return None


class Command(BaseCommand):
    help = "สร้างบัญชีพนักงานสอบสวน (ใช้ระบบได้ทันที ไม่ใช่ผู้ดูแลระบบ)"

    def add_arguments(self, parser):
        for name in ("username", "rank", "first-name", "last-name", "position", "password"):
            parser.add_argument(f"--{name}", default=None)
        parser.add_argument("--role", default=Role.INVESTIGATOR, choices=Role.values)

    # ------------------------------------------------------------------ input
    def read(self, prompt: str, secret: bool = False) -> str:
        try:
            raw = getpass.getpass(prompt) if secret else input(prompt)
        except (EOFError, KeyboardInterrupt):
            raise CommandError("ยกเลิกการสร้างบัญชี ยังไม่ได้บันทึกอะไร") from None
        return repair_console_text(raw).strip()

    def warn(self, text: str):
        self.stderr.write(self.style.ERROR(text))

    def ask(self, opts, key, label, required=True, check=None) -> str:
        """ใช้ค่าจากตัวเลือกบรรทัดคำสั่งถ้ามี ไม่อย่างนั้นถามจนได้ค่าที่บันทึกได้."""
        given = opts.get(key.replace("-", "_"))
        if given is not None:
            value = repair_console_text(given).strip()
            problem = (check or text_problem)(value) if value else ("ต้องระบุ" if required else None)
            if problem:
                raise CommandError(f"--{key}: {problem}")
            return value
        while True:
            value = self.read(f"{label}: ")
            if not value:
                if required:
                    self.warn("ต้องระบุช่องนี้")
                    continue
                return ""
            problem = (check or text_problem)(value)
            if not problem:
                return value
            self.warn(f"{problem} — ลองพิมพ์ใหม่")
            if check is None:
                self.warn(FALLBACK_HINT)

    @staticmethod
    def username_problem(value: str) -> str | None:
        if not USERNAME_RE.match(value):
            return "ชื่อผู้ใช้ใช้ได้เฉพาะตัวอักษรภาษาอังกฤษ ตัวเลข และ @ . + - _ (เปลี่ยนแป้นเป็นภาษาอังกฤษก่อนพิมพ์)"
        if User.objects.filter(username__iexact=value).exists():
            return f"มีชื่อผู้ใช้ {value} ในระบบแล้ว"
        return None

    @staticmethod
    def password_problem(value: str) -> str | None:
        if len(value) < MIN_PASSWORD:
            return f"รหัสผ่านสั้นเกินไป พิมพ์มา {len(value)} ตัว ต้องอย่างน้อย {MIN_PASSWORD} ตัว"
        if not value.isascii() or not value.isprintable():
            return "รหัสผ่านต้องพิมพ์ด้วยแป้นภาษาอังกฤษ (ตัวอักษร ตัวเลข สัญลักษณ์) — กดเปลี่ยนภาษาเป็น EN ก่อนพิมพ์"
        return None

    def ask_password(self, opts) -> str:
        given = opts.get("password")
        if given is not None:
            problem = self.password_problem(given)
            if problem:
                raise CommandError(problem)
            return given
        self.stdout.write(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัว พิมพ์ด้วยแป้นภาษาอังกฤษ (ขณะพิมพ์จะไม่เห็นตัวอักษร)")
        while True:
            first = self.read(f"รหัสผ่าน (อย่างน้อย {MIN_PASSWORD} ตัว): ", secret=True)
            problem = self.password_problem(first)
            if problem:
                self.warn(problem)
                continue
            if self.read("พิมพ์รหัสผ่านซ้ำอีกครั้ง: ", secret=True) != first:
                self.warn("รหัสผ่านสองครั้งไม่ตรงกัน — เริ่มพิมพ์ใหม่")
                continue
            return first

    # ------------------------------------------------------------------ main
    def handle(self, *args, **opts):
        with utf8_line_editing():
            self.create(opts)

    def create(self, opts):
        username = self.ask(opts, "username", "ชื่อผู้ใช้ (ภาษาอังกฤษ เช่น inv1)", check=self.username_problem)
        rank = self.ask(opts, "rank", "ยศ (เช่น ร.ต.อ.)", required=False)
        first = self.ask(opts, "first-name", "ชื่อ")
        last = self.ask(opts, "last-name", "นามสกุล")
        position = self.ask(opts, "position", "ตำแหน่ง (เช่น รอง สว.(สอบสวน))", required=False)
        password = self.ask_password(opts)
        user = User(username=username, rank=rank, first_name=first, last_name=last, position=position, role=opts["role"])
        user.set_password(password)
        try:
            user.save()
        except (DataError, UnicodeError) as exc:
            raise CommandError(f"บันทึกบัญชีไม่สำเร็จ ยังไม่ได้สร้างบัญชี ({exc.__class__.__name__}) — {FALLBACK_HINT}") from None
        self.stdout.write(
            self.style.SUCCESS(f"สร้างบัญชี {user.display_name} ({user.get_role_display()}) แล้ว เข้าสู่ระบบด้วย {username}")
        )
        self.stdout.write("ถ้าชื่อภาษาไทยแสดงผิด แก้ได้ที่เมนู ข้อมูลพื้นฐานของฉัน หลังเข้าสู่ระบบ")
