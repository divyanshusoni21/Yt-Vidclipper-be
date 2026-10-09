import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings
from kombu import Queue

# Set default Django settings module for celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'yt_helper.settings')

# Initialize Celery app for yt_helper project
app = Celery('yt_helper')

# Broker URL: explicit env wins, otherwise the same Redis Django already uses.
def _redis_url():
    fallbackUrl = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
    brokerUrl = os.environ.get('CELERY_BROKER_URL', fallbackUrl)
    resultBackend = os.environ.get('CELERY_RESULT_BACKEND', fallbackUrl)
    return brokerUrl, resultBackend


brokerUrl, resultBackend = _redis_url()

app.conf.update(
    broker_url=brokerUrl,
    result_backend=resultBackend,
    task_default_queue='default',
    task_queues=(
        Queue('video'),
        Queue('default'),
    ),
    task_routes={
        'process_clip_task': {'queue': 'video'},
        'process_speed_edit_task': {'queue': 'video'},
        'send_email_task': {'queue': 'default'},
        'cleanup_old_files_task': {'queue': 'default'},
        'cleanup_cancelled_dir_task': {'queue': 'default'},
    },
    # Ack after the task finishes so a dead worker can redeliver the job.
    task_acks_late=True,
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    # 1 min soft stop, 2 min hard kill.
    task_soft_time_limit=60,
    task_time_limit=120,
    # Cancel needs STARTED vs waiting. Without this a running task still looks PENDING.
    task_track_started=True,
    
    timezone=settings.TIME_ZONE,
    beat_schedule={
        'cleanup-old-files-periodic': {
            'task': 'cleanup_old_files_task',
            'schedule': crontab(minute=0, hour='*/6'),
        },
    },
)

# Load task modules from all registered Django app configs (e.g. home/tasks.py).
app.autodiscover_tasks()
