import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from backend directory and workspace root
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)
else:
    load_dotenv()

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from . import plan_cache  # noqa: E402
from .gemini_client import generate_plan  # noqa: E402
from .policy import (  # noqa: E402
    build_fallback_plan,
    classify_risk,
    sanitize_snapshot,
    validate_plan,
)
from .schemas import (  # noqa: E402
    CommandMessage,
    ConfirmRequest,
    PlanRequest,
    PlanResponse,
)
from .sessions import (  # noqa: E402
    VALID_MODES,
    create_session,
    dispatch,
    get_session,
    log_event,
    process_command,
    stop_session,
)

app = FastAPI(
    title="Ghost Hands Backend",
    description="Permission-scoped, human-in-the-loop AI accessibility companion backend.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


def _flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ghost-hands-backend",
        "version": "1.0.0",
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        "force_fallback": _flag("FORCE_FALLBACK"),
        "accessibility_modes": sorted(list(VALID_MODES)),
    }


@app.post("/plan", response_model=PlanResponse)
def create_plan(request: PlanRequest):
    session = create_session(request.intent)
    safe = sanitize_snapshot(request.page_snapshot)

    if not safe:
        return PlanResponse(
            session_id=session.session_id,
            status="blocked",
            source=None,
            task=request.intent,
            risk="high",
            total_steps=0,
            steps=[],
            message="No usable, non-sensitive elements on this page.",
        )

    safe_dicts = [e.model_dump() for e in safe]
    cache_key = plan_cache.make_key(request.intent, safe_dicts)

    raw, source, notes = None, None, []

    if not _flag("FORCE_FALLBACK"):
        cached = plan_cache.get(cache_key)
        if cached:
            raw, source = cached, "cache"
        else:
            try:
                raw, source = generate_plan(request.intent, safe_dicts), "gemini"
            except Exception as error:
                notes.append(f"Planner unavailable ({type(error).__name__})")

    steps, errors = ([], [])
    if raw:
        steps, errors = validate_plan(raw, safe)
        if steps and source == "gemini":
            plan_cache.put(cache_key, raw)
        if not steps:
            notes.append("Planner output rejected by policy")

    task = (raw or {}).get("task") or request.intent

    if not steps:
        raw = build_fallback_plan(safe, request.intent)
        steps, errors = validate_plan(raw, safe)
        source = "fallback_rules"
        task = raw.get("task", request.intent)

    if not steps:
        return PlanResponse(
            session_id=session.session_id,
            status="blocked",
            source=None,
            task=request.intent,
            risk="high",
            total_steps=0,
            steps=[],
            message="No safe guidance plan could be validated.",
        )

    session.steps = [s.model_dump() for s in steps]
    log_event(session, "plan_created", source=source, steps=len(steps))

    message = "; ".join(notes + errors[:3]) or None
    return PlanResponse(
        session_id=session.session_id,
        status="ok" if source in {"gemini", "cache"} else "fallback",
        source=source,
        task=task,
        risk=classify_risk(steps),
        current_step=1,
        total_steps=len(steps),
        steps=steps,
        message=message,
    )


@app.get("/sessions/{session_id}")
def get_session_state(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "status": session.status,
        "current_step": session.current_step,
        "total_steps": len(session.steps),
        "steps": session.steps,
        "adaptations": session.adaptations,
        "audit": session.audit,
    }


@app.post("/sessions/{session_id}/confirm")
def confirm_step(session_id: str, request: ConfirmRequest):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="Session is not active")
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Confirmation rejected")
    if request.step_id not in {s["step_id"] for s in session.steps}:
        raise HTTPException(status_code=400, detail="Invalid step")

    session.confirmed_steps.add(request.step_id)
    log_event(session, "confirmed", step_id=request.step_id)
    return {"session_id": session_id, "step_id": request.step_id, "confirmed": True}


@app.post("/sessions/{session_id}/command")
async def send_command(session_id: str, command: CommandMessage):
    """Universal Input Bridge HTTP endpoint: execute an accessibility command (switch, tap, voice, external trigger)."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    reply = await dispatch(
        session, command.type, step_id=command.step_id, mode=command.mode
    )
    return reply


@app.post("/sessions/{session_id}/stop")
def stop(session_id: str):
    if not stop_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "status": "stopped"}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = get_session(session_id)
    if not session:
        await websocket.close(code=4404)
        return

    session.websocket = websocket
    await websocket.send_json(
        {
            "type": "CONNECTED",
            "session_id": session_id,
            "current_step": session.current_step,
            "total_steps": len(session.steps),
        }
    )

    try:
        while True:
            try:
                command = CommandMessage(**await websocket.receive_json())
            except WebSocketDisconnect:
                raise
            except ValueError:  # bad JSON or failed validation
                await websocket.send_json(
                    {"type": "ERROR", "detail": "Invalid command"}
                )
                continue

            reply = process_command(
                session, command.type, step_id=command.step_id, mode=command.mode
            )
            await websocket.send_json(reply)

            if reply["type"] == "SESSION_STOPPED":
                await websocket.close()
                break
    except WebSocketDisconnect:
        pass
    finally:
        if session.websocket is websocket:
            session.websocket = None
