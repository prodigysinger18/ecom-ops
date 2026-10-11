import json
from app.core.db import SessionLocal
from app.models import AsyncTask
from app.services.tasks import celery_app, retry_or_dead_letter


if celery_app:
    def _run(task_id: int):
        with SessionLocal() as db:
            task = db.get(AsyncTask, task_id)
            if not task or task.status == "dead_letter":
                return
            try:
                task.status = "processing"
                db.commit()
                # Integrations are deliberately isolated here; replace with platform API calls.
                json.loads(task.payload)
                task.status = "done"
                db.commit()
            except Exception as exc:
                retry_or_dead_letter(db, task_id, str(exc))

    @celery_app.task(name="sync_logistics")
    def sync_logistics(task_id: int):
        return _run(task_id)

    @celery_app.task(name="send_notification")
    def send_notification(task_id: int):
        return _run(task_id)

    @celery_app.task(name="generate_report")
    def generate_report(task_id: int):
        return _run(task_id)
