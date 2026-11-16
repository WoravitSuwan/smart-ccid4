"""นำเข้าคำพิพากษาศาลฎีกาจากไฟล์ CSV หรือ JSON ที่หน่วยงานมีสิทธิ์ใช้ (A4.7) และทำ embedding เก็บใน pgvector.

ห้ามใช้ไฟล์ที่ดึงจากเว็บไซต์โดยไม่ได้รับอนุญาต
คอลัมน์/คีย์: number, year, sections, keywords, summary, full_text
"""

import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from dika import services


class Command(BaseCommand):
    help = "นำเข้าฎีกาจาก CSV/JSON แล้วสร้าง embedding"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--source", default="", help="ชื่อชุดข้อมูล/แหล่งที่มีสิทธิ์ใช้")

    def handle(self, path, source, **opts):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"ไม่พบไฟล์ {p}")
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
            rows = data["rulings"] if isinstance(data, dict) else data
        elif p.suffix.lower() == ".csv":
            with p.open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        else:
            raise CommandError("รองรับเฉพาะ .json และ .csv")
        created, updated = services.import_rows(rows, source or p.name)
        self.stdout.write(f"นำเข้าใหม่ {created} รายการ ปรับปรุง {updated} รายการ")
