from celery import shared_task


@shared_task
def process_task(session_id: int) -> str:
    from .services import process

    return process(session_id).status
