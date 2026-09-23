from __future__ import annotations

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("neo_lead_scraping", broker=redis_url, backend=redis_url)
celery_app.conf.timezone = "Asia/Baku"
celery_app.conf.task_default_retry_delay = 60
