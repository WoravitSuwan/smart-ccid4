from celery import shared_task


@shared_task
def generate_job(job_id: int) -> int:
    from .services import run_job

    return run_job(job_id).pk


@shared_task
def build_bundle(bundle_id: int) -> str:
    from .bundle import build

    return build(bundle_id).status
