from datetime import date

from django.core.management.base import BaseCommand

from deadlines.models import Holiday

# Fixed-date public holidays only. Lunar holidays (มาฆบูชา วิสาขบูชา อาสาฬหบูชา เข้าพรรษา) and special days
# announced by the cabinet change every year: the administrator adds them in /django-admin/deadlines/holiday/.
FIXED = [
    (1, 1, "วันขึ้นปีใหม่"),
    (4, 6, "วันจักรี"),
    (4, 13, "วันสงกรานต์"),
    (4, 14, "วันสงกรานต์"),
    (4, 15, "วันสงกรานต์"),
    (5, 4, "วันฉัตรมงคล"),
    (6, 3, "วันเฉลิมพระชนมพรรษาสมเด็จพระราชินี"),
    (7, 28, "วันเฉลิมพระชนมพรรษาพระบาทสมเด็จพระเจ้าอยู่หัว"),
    (8, 12, "วันแม่แห่งชาติ"),
    (10, 13, "วันนวมินทรมหาราช"),
    (10, 23, "วันปิยมหาราช"),
    (12, 5, "วันพ่อแห่งชาติ"),
    (12, 10, "วันรัฐธรรมนูญ"),
    (12, 31, "วันสิ้นปี"),
]


class Command(BaseCommand):
    help = "เพิ่มวันหยุดราชการที่ตรงวันทุกปี (ปี ค.ศ.) — วันหยุดตามจันทรคติให้ผู้ดูแลเพิ่มเอง"

    def add_arguments(self, parser):
        parser.add_argument("years", nargs="*", type=int)

    def handle(self, *args, years, **opts):
        years = years or [date.today().year, date.today().year + 1]
        n = 0
        for y in years:
            for m, d, name in FIXED:
                n += Holiday.objects.get_or_create(date=date(y, m, d), defaults={"name": name})[1]
        self.stdout.write(f"เพิ่มวันหยุด {n} วัน (ปี {', '.join(str(y + 543) for y in years)})")
