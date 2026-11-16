"""Main menu (spec section 3). Items whose page is not built yet show as "เร็ว ๆ นี้"."""

from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass
class MenuItem:
    key: str
    label: str
    url_name: str
    icon: str
    new: bool = False
    roles: tuple[str, ...] = ()  # empty = everyone except admin-only items

    def url(self):
        try:
            return reverse(self.url_name)
        except NoReverseMatch:
            return None


MENU = [
    MenuItem("home", "ภาพรวม", "core:home", "home"),
    MenuItem("cases", "คดีของฉัน", "cases:list", "folder"),
    MenuItem("intake", "รับแจ้งความ & นำเข้าคดี", "intake:index", "inbox"),
    MenuItem("summons", "หมายเรียก", "summons:index", "mail", new=True),
    MenuItem("interviews", "ห้องสอบปากคำ", "interviews:index", "mic", new=True),
    MenuItem("network", "เครือข่ายคดี", "cyber:network", "share", new=True),
    MenuItem("profile", "ข้อมูลพื้นฐานของฉัน", "accounts:profile", "id"),
    MenuItem("unit", "แดชบอร์ดหน่วยงาน", "dashboard:unit", "chart", new=True, roles=("supervisor", "commander", "admin")),
    MenuItem("settings", "ตั้งค่าระบบ", "core:settings", "settings", roles=("admin",)),
    MenuItem("help", "คู่มือ & แจ้งปัญหา", "core:help", "help"),
]


def menu_for(user):
    items = []
    for m in MENU:
        if m.roles and getattr(user, "role", None) not in m.roles:
            continue
        items.append({"key": m.key, "label": m.label, "url": m.url(), "icon": m.icon, "new": m.new})
    return items
