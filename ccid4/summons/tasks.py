from celery import shared_task


@shared_task
def run_batch_task(batch_id: int) -> str:
    from .services import run_batch

    return run_batch(batch_id).status
