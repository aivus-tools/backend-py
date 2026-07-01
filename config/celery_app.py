import os

from celery import Celery
from celery.signals import setup_logging
from celery.signals import worker_process_init

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("aivus_backend")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from logging.config import dictConfig  # noqa: PLC0415

    from django.conf import settings  # noqa: PLC0415

    dictConfig(settings.LOGGING)


@worker_process_init.connect
def warm_up_llm(*args, **kwargs):
    # Preload the deferred google.genai import per worker process so the first
    # LLM task does not pay the ~13s pydantic schema build inline.
    from aivus_backend.core.llm import warm_up_gemini  # noqa: PLC0415

    warm_up_gemini()


# Load task modules from all registered Django app configs.
app.autodiscover_tasks()
