# Import celery_app to ensure it is always loaded when Django starts
from .celery import app as celery_app

__all__ = ('celery_app',)
