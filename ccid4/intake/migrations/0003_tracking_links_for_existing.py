from datetime import timedelta

from django.db import migrations


def forward(apps, schema_editor):
    """Citizens who reported before tracking links existed keep their link (same token), valid one year from today."""
    from django.utils import timezone

    Sub = apps.get_model("intake", "IntakeSubmission")
    Link = apps.get_model("intake", "TrackingLink")
    until = timezone.now() + timedelta(days=365)
    for s in Sub.objects.filter(case__isnull=False):
        if not Link.objects.filter(token=s.track_token).exists():
            Link.objects.create(case_id=s.case_id, token=s.track_token, reference=s.reference, expires_at=until)


class Migration(migrations.Migration):
    dependencies = [("intake", "0002_trackinglink")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
