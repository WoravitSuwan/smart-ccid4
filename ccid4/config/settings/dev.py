from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-not-secret")
DEMO_MODE = True
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_EAGER", default=True)

WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
