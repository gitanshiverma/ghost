import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket

VALID_MODES = {"visual", "voice", "keyboard", "large_target", "simple", "high_contrast"}


@dataclass
class Session:
    session_id: str
    intent: str
    steps: list[dict] = field(default_factory=list)
    current_step: int = 1
    status: str = "active"  # active | completed | stopped
    websocket: Optional[WebSocket] = None
    confirmed_steps: set[int] = field(default_factory=set)
    adaptations: list[str] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)  # events only, never field values
    created_at: float = field(default_factory=time.time)


sessions: dict[str, Session] = {}


def create_session(intent: str) -> Session:
    session = Session(session_id=str(uuid.uuid4()), intent=intent)
    sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> Optional[Session]:
    return sessions.get(session_id)


def latest_active_session() -> Optional[Session]:
    active = [s for s in sessions.values() if s.status == "active" and s.steps]
    return max(active, key=lambda s: s.created_at) if active else None


def log_event(session: Session, event: str, **data) -> None:
    session.audit.append({"t": round(time.time(), 2), "event": event, **data})


def stop_session(session_id: str) -> bool:
    session = get_session(session_id)
    if not session:
        return False
    session.status = "stopped"
    session.steps = []
    session.current_step = 0
    log_event(session, "stopped")
    return True


def _current(session: Session) -> Optional[dict]:
    if 1 <= session.current_step <= len(session.steps):
        return session.steps[session.current_step - 1]
    return None


def _show_step(session: Session) -> dict:
    return {
        "type": "SHOW_STEP",
        "current_step": session.current_step,
        "total_steps": len(session.steps),
        "step": session.steps[session.current_step - 1],
    }


def _complete_payload(session: Session) -> dict:
    return {
        "type": "TASK_COMPLETE",
        "total_steps": len(session.steps),
        "adaptations": len(session.adaptations),
        "summary": (
            f"Task complete. {len(session.steps)} steps. "
            f"{len(session.adaptations)} accessibility adaptation(s)."
        ),
    }


def process_command(
    session: Session,
    cmd_type: str,
    step_id: Optional[int] = None,
    mode: Optional[str] = None,
) -> dict:
    """Universal Input Bridge: processes commands from WebSocket, keyboard/voice events, or REST API."""
    cmd = (cmd_type or "").upper()

    if session.status == "stopped":
        return {"type": "SESSION_STOPPED"}
    if session.status == "completed":
        return _complete_payload(session)

    if cmd == "STOP":
        stop_session(session.session_id)
        return {"type": "SESSION_STOPPED"}

    step = _current(session)
    if step is None:
        return {"type": "ERROR", "detail": "No active step"}

    if cmd == "REPEAT":
        return _show_step(session)

    if cmd in {"PREV", "BACK", "PREVIOUS"}:
        if session.current_step > 1:
            session.current_step -= 1
            log_event(session, "step_back", step_id=session.current_step)
        return _show_step(session)

    if cmd == "ADAPTATION_REQUESTED":
        if mode not in VALID_MODES:
            return {"type": "ERROR", "detail": f"Unknown mode: {mode}"}
        session.adaptations.append(mode)
        log_event(session, "adaptation", mode=mode)
        return {
            "type": "ADAPTATION_APPLIED",
            "mode": mode,
            "adaptations": len(session.adaptations),
        }

    if cmd == "CONFIRM":
        session.confirmed_steps.add(step["step_id"])
        log_event(session, "confirmed", step_id=step["step_id"])
        return {"type": "CONFIRMED", "step_id": step["step_id"]}

    if cmd in {"NEXT", "STEP_COMPLETED"}:
        # Ignore stale/duplicate completions and just resync the client.
        if cmd == "STEP_COMPLETED" and step_id is not None and step_id != step["step_id"]:
            return _show_step(session)

        if step.get("requires_confirmation") and step["step_id"] not in session.confirmed_steps:
            log_event(session, "blocked_unconfirmed", step_id=step["step_id"])
            return {
                "type": "CONFIRMATION_REQUIRED",
                "step_id": step["step_id"],
                "caption": step.get("short_caption", ""),
            }

        log_event(session, "step_done", step_id=step["step_id"])
        if session.current_step < len(session.steps):
            session.current_step += 1
            return _show_step(session)

        session.status = "completed"
        return _complete_payload(session)

    return {"type": "ERROR", "detail": f"Unsupported command: {cmd_type}"}


async def push(session: Session, message: dict) -> None:
    if session.websocket is None:
        return
    try:
        await session.websocket.send_json(message)
    except Exception:
        session.websocket = None


async def dispatch(session: Session, cmd_type: str, **kwargs) -> dict:
    """Interpret a command and push the reply to the session's WebSocket."""
    reply = process_command(session, cmd_type, **kwargs)
    await push(session, reply)
    return reply
