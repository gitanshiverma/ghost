import re

from .schemas import PageElement, PlannedStep

MAX_STEPS = 10
MAX_ELEMENTS = 150
MAX_TEXT = 120

# Credential firewall. Deliberately specific so harmless text like "Admit card" or "PIN code"
# (postal code) is NOT blocked, while "Card number", "UPI PIN", "OTP", "CVV" are.
SENSITIVE_PATTERNS = [
    r"password",
    r"passcode",
    r"\botp\b",
    r"one[- ]time (password|code|pin)",
    r"\bpin\b(?!\s*code)",
    r"upi[- ]?pin",
    r"\bcvv\b",
    r"\bcvc\b",
    r"card number",
    r"credit card",
    r"debit card",
    r"aadhaar",
    r"aadhar",
    r"passport number",
    r"secret",
    r"api[- ]?key",
    r"(auth|access|api|session) token",
    r"security answer",
    r"\bssn\b",
    r"bank account",
]
_SENSITIVE_RE = re.compile("|".join(f"(?:{p})" for p in SENSITIVE_PATTERNS), re.IGNORECASE)

# Buttons whose label means "this changes something for real" -> user must confirm first.
_CONFIRM_RE = re.compile(
    r"\b(submit|pay|payment|delete|remove|send|confirm|transfer|purchase|buy|"
    r"checkout|place order|apply|save changes|unsubscribe|post)\b",
    re.IGNORECASE,
)

VALID_SIGNALS = {"input_change", "change", "click", "navigation", "user_confirmation"}


def contains_sensitive_reference(text: str) -> bool:
    return bool(_SENSITIVE_RE.search((text or "").lower()))


def is_confirm_label(text: str) -> bool:
    return bool(_CONFIRM_RE.search((text or "").lower()))


def _labels(el: PageElement) -> str:
    return " ".join(
        filter(None, [el.accessible_name, el.visible_label, el.placeholder])
    )


def is_sensitive_element(el: PageElement) -> bool:
    if el.is_sensitive:
        return True
    if (el.input_type or "").lower() == "password":
        return True
    return contains_sensitive_reference(_labels(el))


def _clip(value):
    if value is None:
        return None
    return str(value)[:MAX_TEXT]


def sanitize_snapshot(snapshot: list[PageElement]) -> list[PageElement]:
    """Server-side second firewall: drop hidden + sensitive elements, clip text, cap count.
    Anything removed here never reaches the LLM and can never be targeted by a step."""
    clean = []
    for el in snapshot:
        if not el.visible or is_sensitive_element(el):
            continue
        clean.append(
            el.model_copy(
                update={
                    "ref": str(el.ref)[:64],
                    "tag": str(el.tag)[:32],
                    "role": _clip(el.role),
                    "accessible_name": _clip(el.accessible_name),
                    "visible_label": _clip(el.visible_label),
                    "placeholder": _clip(el.placeholder),
                    "input_type": _clip(el.input_type),
                    "selector": _clip(el.selector),
                }
            )
        )
        if len(clean) >= MAX_ELEMENTS:
            break
    return clean


def needs_confirmation(step: PlannedStep, el: PageElement) -> bool:
    if step.action_type == "submit_form":
        return True
    if (el.input_type or "").lower() == "submit":
        return True
    if step.action_type == "click_button":
        return is_confirm_label(
            " ".join(filter(None, [_labels(el), step.instruction, step.short_caption]))
        )
    return False


def validate_plan(
    plan: dict, page_snapshot: list[PageElement]
) -> tuple[list[PlannedStep], list[str]]:
    errors: list[str] = []
    validated: list[PlannedStep] = []

    element_by_ref = {el.ref: el for el in page_snapshot}
    raw_steps = plan.get("steps") or []

    if not isinstance(raw_steps, list) or not raw_steps:
        return [], ["No steps returned"]

    raw_steps = raw_steps[:MAX_STEPS]

    for index, raw in enumerate(raw_steps, start=1):
        try:
            step = PlannedStep(**raw)
        except Exception as error:  # pydantic ValidationError / TypeError
            errors.append(f"Invalid step {index}: {error}")
            continue

        el = element_by_ref.get(step.target_ref)
        if el is None:
            errors.append(f"Unknown target_ref: {step.target_ref}")
            continue
        if not el.visible:
            errors.append(f"Target is not visible: {step.target_ref}")
            continue
        if is_sensitive_element(el):
            errors.append(f"Sensitive target rejected: {step.target_ref}")
            continue

        text = " ".join([step.instruction, step.short_caption, _labels(el)])
        if contains_sensitive_reference(text):
            errors.append(f"Sensitive reference in step {index}")
            continue

        # Set resolved target_selector if not explicitly specified
        if not step.target_selector:
            step.target_selector = el.selector or f"[data-ghost-ref='{el.ref}']"

        # The model can only ADD confirmation, never remove it.
        step.requires_confirmation = step.requires_confirmation or needs_confirmation(
            step, el
        )
        if step.completion_signal not in VALID_SIGNALS:
            step.completion_signal = "user_confirmation"

        validated.append(step)

    # Renumber so step_id always equals position (the WebSocket logic relies on this).
    for position, step in enumerate(validated, start=1):
        step.step_id = position

    if not validated:
        errors.append("No safe steps could be validated")

    return validated, errors


def classify_risk(steps: list[PlannedStep]) -> str:
    if any(s.requires_confirmation for s in steps):
        return "high"
    if any(s.action_type in {"upload_file", "select_option"} for s in steps):
        return "medium"
    return "low"


def build_fallback_plan(snapshot: list[PageElement], intent: str) -> dict:
    """Rule-based planner used when Gemini is down, slow, or returns nothing safe.
    Walks the form top-to-bottom: fields -> upload -> submit-like button last."""
    steps: list[dict] = []
    submit_el = None
    alts = ["keyboard: Tab then Enter", "voice: say done", "large_target: assist button"]

    for el in snapshot:
        if not el.visible or is_sensitive_element(el):
            continue
        tag = (el.tag or "").lower()
        itype = (el.input_type or "").lower()
        label = (
            el.visible_label or el.accessible_name or el.placeholder or tag
        ).strip()[:60]
        selector = el.selector or f"[data-ghost-ref='{el.ref}']"

        if tag == "input" and itype == "file":
            steps.append(
                dict(
                    target_ref=el.ref,
                    target_selector=selector,
                    action_type="upload_file",
                    instruction=f"Upload your file here: {label}.",
                    short_caption=f"Upload {label}"[:40],
                    completion_signal="change",
                )
            )
        elif tag == "select":
            steps.append(
                dict(
                    target_ref=el.ref,
                    target_selector=selector,
                    action_type="select_option",
                    instruction=f"Choose an option for {label}.",
                    short_caption=f"Choose {label}"[:40],
                    completion_signal="change",
                )
            )
        elif tag in {"input", "textarea"} and itype in {"checkbox", "radio"}:
            steps.append(
                dict(
                    target_ref=el.ref,
                    target_selector=selector,
                    action_type="click_button",
                    instruction=f"Tick this box: {label}.",
                    short_caption=f"Tick {label}"[:40],
                    completion_signal="click",
                )
            )
        elif tag in {"input", "textarea"} and itype not in {
            "button", "submit", "hidden", "reset", "image",
        }:
            req = " (required)" if el.required else ""
            steps.append(
                dict(
                    target_ref=el.ref,
                    target_selector=selector,
                    action_type="fill_field",
                    instruction=f"Enter your {label} here{req}.",
                    short_caption=f"Enter {label}"[:40],
                    completion_signal="input_change",
                )
            )
        elif tag == "button" or itype == "submit" or (el.role or "") == "button":
            if itype == "submit" or is_confirm_label(label):
                submit_el = el  # keep the last submit-like button

    steps = steps[: MAX_STEPS - 1]

    if submit_el is not None:
        label = (
            submit_el.visible_label or submit_el.accessible_name or "Submit"
        ).strip()[:60]
        selector = submit_el.selector or f"[data-ghost-ref='{submit_el.ref}']"
        steps.append(
            dict(
                target_ref=submit_el.ref,
                target_selector=selector,
                action_type="submit_form",
                instruction=f"Check everything, then press '{label}' yourself.",
                short_caption=f"Press {label}"[:40],
                requires_confirmation=True,
                completion_signal="click",
            )
        )

    for i, step in enumerate(steps, start=1):
        step["step_id"] = i
        step.setdefault("mode", "visual")
        step.setdefault("alternatives", alts)
        step.setdefault("requires_confirmation", False)

    return {"task": intent[:80] or "Guided task", "risk": "medium", "steps": steps}
