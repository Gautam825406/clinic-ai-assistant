"""Voice router — telephony webhook, simulated call, live text stream.

Sibling modules (telephony, dialogue) are imported lazily inside the
endpoints: if a sibling has not landed yet, the endpoint returns
503 "module not ready" instead of crashing app startup (CONTRACT).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(tags=["voice"])

# In-memory live-stream sessions: call_id -> dialogue session dict.
_stream_sessions: dict[str, dict] = {}


class SimulateRequest(BaseModel):
    tenant_id: str | None = None
    from_number: str = "+919000000001"
    text: str = ""
    turns: list[str] | None = None


def _not_ready():
    raise HTTPException(status_code=503, detail="module not ready — dialogue/telephone module abhi build ho raha hai")


@router.post("/webhooks/telephony/call")
async def telephony_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        from ..telephony import handle_inbound_webhook
    except Exception:
        _not_ready()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return handle_inbound_webhook(payload, db)


@router.post("/voice/simulate")
def simulate_call(body: SimulateRequest, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    tenant_id = body.tenant_id or user.tenant_id
    if tenant_id != user.tenant_id and not user.is_platform_admin:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from ..dialogue import DialogueEngine
    except Exception:
        _not_ready()
    from .common import clinic_for

    clinic = clinic_for(db, tenant_id)
    engine = DialogueEngine(db, tenant_id, clinic)
    turns = body.turns if body.turns else ([body.text] if body.text else [])
    if not turns:
        raise HTTPException(status_code=400, detail="text ya turns mein se ek dein")
    call_id = f"sim-{uuid.uuid4().hex[:12]}"
    session: dict = {"call_id": call_id, "from_number": body.from_number}
    transcript: list[dict] = []
    appointment_id = None
    last: dict = {}
    for turn in turns:
        transcript.append({"role": "patient", "text": turn})
        last = engine.process_text(session, turn) or {}
        transcript.append({"role": "assistant", "text": last.get("reply", "")})
        if last.get("appointment_id"):
            appointment_id = last["appointment_id"]
    appointment = None
    if appointment_id:
        from .common import appointment_for

        try:
            appointment = appointment_for(db, tenant_id, appointment_id)
            from .common import appointment_dict

            appointment = appointment_dict(db, appointment)
        except HTTPException:
            appointment = None
    return {"call_id": call_id, "tenant_id": tenant_id,
            "transcript": transcript, "state": last.get("state"),
            "language": session.get("language") or "hinglish",
            "appointment_id": appointment_id, "appointment": appointment}


@router.websocket("/voice/stream/{call_id}")
async def voice_stream(websocket: WebSocket, call_id: str):
    await websocket.accept()
    session = _stream_sessions.setdefault(call_id, {"call_id": call_id})
    try:
        from ..dialogue import DialogueEngine
    except Exception:
        await websocket.send_json(
            {"reply": "Dialogue module abhi ready nahi hai (module not ready).",
             "state": "NOT_READY"})
        await websocket.close()
        return
    db = SessionLocal()
    try:
        tenant_id = websocket.query_params.get("tenant_id") or ""
        api_key = websocket.query_params.get("api_key") or ""
        user = None
        if api_key:
            user = db.query(User).filter(User.api_token == api_key).first()
        if user is not None:
            tenant_id = user.tenant_id
        if not tenant_id:
            await websocket.send_json(
                {"reply": "tenant_id ya api_key query param dein.", "state": "ERROR"})
            await websocket.close()
            return
        from .common import clinic_for

        clinic = clinic_for(db, tenant_id)
        engine = DialogueEngine(db, tenant_id, clinic)
        session.setdefault("tenant_id", tenant_id)
        while True:
            try:
                text = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            result = engine.process_text(session, text) or {}
            await websocket.send_json({
                "reply": result.get("reply", ""),
                "state": result.get("state"),
                "action": result.get("action"),
                "appointment_id": result.get("appointment_id"),
            })
    finally:
        db.close()
        _stream_sessions.pop(call_id, None)
