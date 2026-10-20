"""Səsli izah düyməsi (app/brief.py): POST JSON qaytarır (mətn + mp3 linki), GET mp3-ün özünü."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app import brief
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.db.models import LeadBrief, LeadRecord, User
from app.outreach import OutreachError

router = APIRouter()


@router.post("/leads/{dedupe_key}/brief", dependencies=[Depends(verify_csrf)])
def lead_brief(dedupe_key: str, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        lead = db.get(LeadRecord, dedupe_key[:64])
        if lead is None:
            return JSONResponse({"error": "Lead tapılmadı"}, status_code=404)
        try:
            row = brief.get_or_create(db, lead)
        except OutreachError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        # created_at versiya kimi — brauzer köhnə mp3-ü keşdən verməsin.
        version = int(row.created_at.timestamp())
        return {"script": row.script, "audio_url": f"/leads/{lead.dedupe_key}/brief.mp3?v={version}"}


@router.get("/leads/{dedupe_key}/brief.mp3")
def lead_brief_audio(dedupe_key: str, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        row = db.get(LeadBrief, dedupe_key[:64])
        if row is None or not row.audio:
            return Response(status_code=404)
        return Response(content=row.audio, media_type="audio/mpeg",
                        headers={"Cache-Control": "private, max-age=86400"})
