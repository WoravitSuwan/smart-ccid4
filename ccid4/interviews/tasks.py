from celery import shared_task


@shared_task
def process_task(session_id: int, job: str | None = None) -> str:
    from .services import process

    return process(session_id, job).status
