from app.policy import (
    build_fallback_plan,
    classify_risk,
    contains_sensitive_reference,
    sanitize_snapshot,
    validate_plan,
)
from app.schemas import PageElement


def el(ref, tag="input", label=None, **kw):
    return PageElement(
        ref=ref, tag=tag, visible_label=label, accessible_name=label, **kw
    )


def step(ref, action="fill_field", **kw):
    base = dict(
        step_id=1, target_ref=ref, action_type=action,
        instruction="Do the thing here.", short_caption="Do thing",
    )
    base.update(kw)
    return base


def test_sensitive_patterns():
    for text in ["Enter your password", "OTP", "UPI PIN", "CVV", "Card number", "Aadhaar number"]:
        assert contains_sensitive_reference(text), text


def test_harmless_text_not_blocked():
    for text in ["Admit card", "PIN code", "Roll number", "Upload income certificate"]:
        assert not contains_sensitive_reference(text), text


def test_sanitize_drops_sensitive_and_hidden():
    snap = [
        el("a", label="Roll Number"),
        el("b", label="Portal password", input_type="password"),
        el("c", label="Enter OTP"),
        el("d", label="Hidden thing", visible=False),
        el("e", label="Marked sensitive", is_sensitive=True),
    ]
    assert [e.ref for e in sanitize_snapshot(snap)] == ["a"]


def test_unknown_target_ref_rejected():
    steps, errors = validate_plan({"steps": [step("nope")]}, [el("a", label="Name")])
    assert steps == []
    assert any("Unknown target_ref" in e for e in errors)


def test_sensitive_target_rejected():
    snap = [el("a", label="Roll Number"), el("p", label="Password", input_type="password")]
    steps, errors = validate_plan({"steps": [step("a"), step("p")]}, snap)
    assert [s.target_ref for s in steps] == ["a"]
    assert any("Sensitive target" in e for e in errors)


def test_sensitive_instruction_rejected():
    snap = [el("a", label="Name")]
    plan = {"steps": [step("a", instruction="Type your OTP here")]}
    steps, _ = validate_plan(plan, snap)
    assert steps == []


def test_submit_always_requires_confirmation():
    snap = [el("s", tag="button", label="Go")]
    steps, _ = validate_plan({"steps": [step("s", "submit_form")]}, snap)
    assert steps[0].requires_confirmation is True


def test_model_cannot_remove_confirmation_for_payment_button():
    snap = [el("s", tag="button", label="Pay now")]
    plan = {"steps": [step("s", "click_button", requires_confirmation=False)]}
    steps, _ = validate_plan(plan, snap)
    assert steps[0].requires_confirmation is True


def test_harmless_button_does_not_need_confirmation():
    snap = [el("u", tag="button", label="Upload document")]
    steps, _ = validate_plan({"steps": [step("u", "click_button")]}, snap)
    assert steps[0].requires_confirmation is False


def test_steps_renumbered_after_drop():
    snap = [el("a", label="A"), el("b", label="B")]
    plan = {"steps": [step("a", step_id=1), step("ghost", step_id=2), step("b", step_id=3)]}
    steps, _ = validate_plan(plan, snap)
    assert [s.step_id for s in steps] == [1, 2]


def test_max_steps_capped():
    snap = [el(f"e{i}", label=f"F{i}") for i in range(15)]
    plan = {"steps": [step(f"e{i}", step_id=i + 1) for i in range(15)]}
    steps, _ = validate_plan(plan, snap)
    assert len(steps) == 10


def test_invalid_completion_signal_normalised():
    snap = [el("a", label="A")]
    steps, _ = validate_plan({"steps": [step("a", completion_signal="bogus")]}, snap)
    assert steps[0].completion_signal == "user_confirmation"


def test_risk_classification():
    snap = [el("a", label="A"), el("s", tag="button", label="Submit")]
    steps, _ = validate_plan({"steps": [step("a"), step("s", "submit_form")]}, snap)
    assert classify_risk(steps) == "high"
    assert classify_risk(steps[:1]) == "low"


def test_fallback_plan_orders_fields_then_submit_and_skips_secrets():
    snap = [
        el("pw", label="Password", input_type="password"),
        el("roll", label="Roll Number", input_type="text", required=True),
        el("file", label="Income certificate", input_type="file"),
        el("btn", tag="button", label="Submit Application"),
    ]
    clean = sanitize_snapshot(snap)
    steps, _ = validate_plan(build_fallback_plan(clean, "scholarship"), clean)
    assert [s.target_ref for s in steps] == ["roll", "file", "btn"]
    assert steps[0].action_type == "fill_field"
    assert steps[1].action_type == "upload_file"
    assert steps[2].action_type == "submit_form"
    assert steps[2].requires_confirmation is True
