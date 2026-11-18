from celery import shared_task


@shared_task
def deadline_alerts() -> int:
    from .alerts import run_alerts

    return run_alerts()


@shared_task
def daily_digest() -> int:
    from .alerts import send_digests

    return send_digests()
