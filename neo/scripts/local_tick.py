"""Mac worker — Azərbaycan IP-si tələb edən mənbələr (sources.LOCAL_ONLY: etender, oxu, report, ...).

Celery/Redis əvəzinə: macOS launchd hər 10 dəqiqədən bir bu skripti işə salır (bax
scripts/install_mac_worker.sh). Hər işə düşəndə:
1. son işləmədən bəri (ən çox 24 saat — Mac yuxuda olubsa) cədvəli düşən LOCAL_ONLY mənbələr;
2. paneldəki "Scrape indi" sorğuları (Modal Queue `neo-local-requests`, app/triggers.py).
Mənbələr ardıcıl işləyir; launchd əvvəlki işləmə bitməmiş yenisini başlatmır.

Gizli açarlar repoda yox: ~/.neo-worker.env (chmod 600) — DATABASE_URL (Neon pooled), OPENAI_API_KEY.
Server alınanda (VPS, Azərbaycan) eyni skript systemd timer ilə işləyəcək.

İstifadə: python -m scripts.local_tick            # cədvəl + panel sorğuları
          python -m scripts.local_tick --only oxu # bir mənbəni indi işlət (yoxlama)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ENV_FILE = Path(os.environ.get("NEO_WORKER_ENV", Path.home() / ".neo-worker.env"))
STATE_FILE = Path.home() / ".neo-local-tick.json"
TICK_MINUTES = 10
MAX_CATCHUP_MIN = 24 * 60

os.environ.setdefault("NEO_SCRAPE_ROLE", "local")
if ENV_FILE.exists():
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)


def window_since(now: datetime, last: str | None) -> int:
    if not last:
        return TICK_MINUTES
    gap = int((now - datetime.fromisoformat(last)).total_seconds() // 60)
    return 0 if gap <= 0 else min(gap, MAX_CATCHUP_MIN)


def panel_requests() -> list[str]:
    """Paneldən gələn LOCAL_ONLY "Scrape indi" sorğuları (Modal Queue). Modal əlçatan deyilsə — boş."""
    try:
        from app import triggers

        items = triggers.local_queue().get_many(100, block=False)
    except Exception as exc:  # şəbəkə / Modal girişi yoxdur — planlı işləmə davam edir
        print(f"panel queue unavailable: {type(exc).__name__}", flush=True)
        return []
    return [key for key, _trigger in items]


def main(argv: list[str]) -> int:
    if not os.environ.get("DATABASE_URL"):
        print(f"DATABASE_URL yoxdur — {ENV_FILE} faylını yarat", flush=True)
        return 2
    from app.scheduler import due_sources, scrape_source
    from app.sources import LOCAL_ONLY

    if "--only" in argv:
        jobs = [(argv[argv.index("--only") + 1], "manual")]
    else:
        now = datetime.now(ZoneInfo("Asia/Baku")).replace(second=0, microsecond=0)
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        window = window_since(now, state.get("last"))
        due = due_sources(now, window) if window else []
        STATE_FILE.write_text(json.dumps({"last": now.isoformat()}))
        jobs = [(k, "schedule") for k in due] + [(k, "manual") for k in panel_requests() if k not in due]
        print(f"{now:%d.%m %H:%M} window={window}m due={due} panel={[k for k, t in jobs if t == 'manual']}", flush=True)

    failed = 0
    for key, trigger in jobs:
        if key not in LOCAL_ONLY:
            print(f"  skip {key}: Modal-da işləyir", flush=True)
            continue
        try:
            pending = scrape_source.run(key, trigger)
            print(f"  {key}: ok, {pending} validasiyaya", flush=True)
        except Exception as exc:  # bir mənbənin xətası digərlərini dayandırmasın (run scrape_runs-a yazılıb)
            failed += 1
            print(f"  {key}: FAILED {type(exc).__name__}: {str(exc)[:200]}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
