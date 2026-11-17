from django.db import migrations

STATUSES = ["สิ้นสุด", "อายัดบัญชี", "พบพนักงานสอบสวน", "ออกหมายเรียก", "ส่งสำนวนอัยการ", "ระหว่างดำเนินการ", "จับกุม", "ออกหมายจับ"]


def seed(apps, schema_editor):
    Status = apps.get_model("summons", "RegisterStatus")
    for name in STATUSES:
        Status.objects.get_or_create(name=name, defaults={"is_final": name == "สิ้นสุด"})


class Migration(migrations.Migration):
    dependencies = [("summons", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
