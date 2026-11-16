from django.db import migrations

from deadlines.rules import DEFAULTS


def seed(apps, schema_editor):
    Rule = apps.get_model("deadlines", "DeadlineRule")
    Version = apps.get_model("deadlines", "DeadlineRuleVersion")
    for key, params in DEFAULTS.items():
        rule, created = Rule.objects.get_or_create(key=key, defaults={"name": params["name"], "params": params, "note": "ค่าตั้งต้น รอฝ่ายกฎหมายตรวจทาน"})
        if created:
            Version.objects.create(rule=rule, version=1, params=params, note="ค่าตั้งต้น")


class Migration(migrations.Migration):
    dependencies = [("deadlines", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
