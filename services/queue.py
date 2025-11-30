from services.celery_app import celery_app

def enqueue_render_job(job_id: str):
    celery_app.send_task(
        "tasks.render_task.render_job_task",
        args=[job_id],
        queue="renderers",
        priority=5
    )
