from celery import shared_task


@shared_task
def generate_job(job_id: int) -> int:
    from .services import run_job

    return run_job(job_id).pk
