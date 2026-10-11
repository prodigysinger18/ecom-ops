import json
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models import AsyncTask

logger = logging.getLogger(__name__)
celery_app = None
if settings.celery_broker_url:
    from celery import Celery
    celery_app = Celery("ecom_ops", broker=settings.celery_broker_url,
                        backend=settings.celery_result_backend or settings.celery_broker_url)


def enqueue(db: Session, task_type: str, payload: dict, idempotency_key: str, max_attempts: int = 3):
    existing = db.scalar(select(AsyncTask).where(AsyncTask.task_key == idempotency_key))
    if existing:
        return existing, False
    task = AsyncTask(task_key=idempotency_key, task_type=task_type, payload=json.dumps(payload, ensure_ascii=False), max_attempts=max_attempts)
    db.add(task)
    db.commit()
    db.refresh(task)
    if celery_app:
        celery_app.send_task(task_type, args=[task.id])
    return task, True


def retry_or_dead_letter(db: Session, task_id: int, error: str):
    task = db.get(AsyncTask, task_id)
    if not task:
        return None
    task.attempts += 1
    task.last_error = error
    task.status = "queued" if task.attempts < task.max_attempts else "dead_letter"
    db.commit()
    return task
