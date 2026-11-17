from celery import shared_task


@shared_task
def process_submission_task(submission_id: int) -> str:
    from .models import IntakeSubmission
    from .services import process_submission

    sub = IntakeSubmission.objects.select_related("link__owner").get(pk=submission_id)
    return process_submission(sub).status


@shared_task
def process_pending() -> int:
    """Safety net (Celery beat, every minute): pick up submissions whose immediate task was lost."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import IntakeSubmission

    cutoff = timezone.now() - timedelta(minutes=2)
    ids = list(IntakeSubmission.objects.filter(status="pending", created_at__lt=cutoff).values_list("pk", flat=True)[:50])
    for pk in ids:
        process_submission_task.delay(pk)
    return len(ids)
