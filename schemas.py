from typing import Literal, Optional

from pydantic import BaseModel, Field


class PageElement(BaseModel):
    ref: str
    tag: str
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    visible_label: Optional[str] = None
    placeholder: Optional[str] = None
    input_type: Optional[str] = None
    selector: Optional[str] = None
    required: bool = False
    is_sensitive: bool = False
    visible: bool = True


class PlanRequest(BaseModel):
    # Session ID is server-generated; any incoming value is optional
    session_id: Optional[str] = None
    intent: str
    page_snapshot: list[PageElement]
    accessibility_mode: Literal[
        "visual", "voice", "keyboard", "large_target", "simple", "high_contrast"
    ] = "visual"


class PlannedStep(BaseModel):
    step_id: int
    target_ref: str
    target_selector: Optional[str] = None
    action_type: Literal[
        "fill_field",
        "select_option",
        "click_button",
        "upload_file",
        "review_information",
        "submit_form",
        "wait_for_user",
    ]
    instruction: str
    short_caption: str
    mode: str = "visual"
    alternatives: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    completion_signal: str = "user_confirmation"


class PlanResponse(BaseModel):
    session_id: str
    status: Literal["ok", "fallback", "blocked"]
    source: Optional[str] = None  # gemini | cache | fallback_rules | None
    task: str
    risk: Literal["low", "medium", "high"]
    current_step: int = 1
    total_steps: int
    steps: list[PlannedStep]
    message: Optional[str] = None


class ConfirmRequest(BaseModel):
    step_id: int
    confirmed: bool


class CommandMessage(BaseModel):
    type: Literal[
        "NEXT",
        "PREV",
        "CONFIRM",
        "REPEAT",
        "STOP",
        "STEP_COMPLETED",
        "ADAPTATION_REQUESTED",
    ]
    step_id: Optional[int] = None
    mode: Optional[str] = None
