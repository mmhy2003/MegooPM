"""Celery task modules.

Task modules live here and are registered with the Celery app via
``TASK_MODULES`` in :mod:`app.core.celery_app`. Importing a module runs its
``@celery_app.task`` decorators, registering the tasks.
"""
