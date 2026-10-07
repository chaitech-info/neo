#!/usr/bin/env bash
# Avtomatik 24/7 scraping: Celery worker + beat (cədvəl) bir prosesdə.
# Tələb: Redis işləyir (`brew services start redis`), .env-də REDIS_URL (defolt localhost:6379/0).
#
# --pool threads: macOS-da fork + OpenAI/httpx/SQLite problemlərindən qaçır; mənbələr
# I/O-dur (şəbəkə + AI), thread-lər kifayətdir. --concurrency 4: eyni anda 4 mənbə.
# Cədvəl: app/scheduler.py::beat_schedule (Asia/Baku vaxtı).
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec celery -A app.scheduler:celery_app worker --beat \
  --pool threads --concurrency 4 \
  --schedule .celerybeat-schedule \
  --loglevel info
