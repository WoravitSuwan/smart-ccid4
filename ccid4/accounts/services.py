"""ข้อมูลพื้นฐานของฉัน (A3): completeness drives the menu dot and document readiness."""

from dataclasses import dataclass

from .models import Unit

SECTIONS = [
    ("personal", "ข้อมูลส่วนตัว"),
    ("commanders", "ผู้บังคับบัญชา"),
    ("unit", "หน่วยงาน"),
    ("arrest_team", "ชุดจับกุม"),
    ("courts", "ศาล/อัยการ"),
]


@dataclass
class Completeness:
    done: dict
    missing_fields: dict

    @property
    def percent(self) -> int:
        return round(100 * sum(self.done.values()) / len(self.done))

    @property
    def missing_count(self) -> int:
        return sum(1 for v in self.done.values() if not v)


def completeness(user) -> Completeness:
    missing = {}
    personal = [
        f
        for f, label in (
            ("rank", "ยศ"),
            ("position", "ตำแหน่ง"),
            ("first_name", "ชื่อ"),
            ("last_name", "นามสกุล"),
            ("phone", "โทรศัพท์"),
        )
        if not getattr(user, f)
    ]
    missing["personal"] = [
        dict(rank="ยศ", position="ตำแหน่ง", first_name="ชื่อ", last_name="นามสกุล", phone="โทรศัพท์")[f] for f in personal
    ]
    missing["commanders"] = [] if user.commanders.exists() else ["ผู้บังคับบัญชาอย่างน้อย 1 คน"]
    unit = Unit.objects.filter(user=user).first()
    missing["unit"] = ["ชื่อหน่วย ที่อยู่ และเลขที่หนังสือ"] if not unit else []
    missing["arrest_team"] = [] if user.arrest_team.exists() else ["เจ้าหน้าที่ชุดจับกุมอย่างน้อย 1 คน"]
    kinds = set(user.courts.values_list("kind", flat=True))
    missing["courts"] = [label for k, label in (("court", "ศาล"), ("prosecutor", "สำนักงานอัยการ")) if k not in kinds]
    return Completeness(done={k: not v for k, v in missing.items()}, missing_fields=missing)
