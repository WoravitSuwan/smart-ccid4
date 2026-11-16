"""First request on a fresh Vercel deployment: create the tables and fill them with demo data (once)."""

import logging

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

log = logging.getLogger(__name__)
LOCK = 4242999


def ensure_ready() -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", [LOCK])  # parallel cold starts wait for the first one
    try:
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            log.info("preview: applying migrations")
            call_command("migrate", interactive=False, verbosity=0)
        from accounts.models import User

        if not User.objects.exists():
            log.info("preview: loading demo data")
            call_command("seed_demo", force=True, verbosity=0)
            call_command("seed_cases", force=True, verbosity=0)
            call_command("seed_holidays", verbosity=0)
            from django.conf import settings

            call_command(
                "import_dika", str(settings.BASE_DIR / "sample_data" / "dika_sample.json"), source="ข้อมูลจำลอง", verbosity=0
            )
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK])
