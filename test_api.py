from fastapi.testclient import TestClient

from app import main
from app.sessions import create_session, process_command

SNAPSHOT = [
    {
        "ref": "el_1",
        "tag": "input",
        "role": "textbox",
        "accessible_name": "Roll Number",
        "visible_label": "Roll Number",
        "input_type": "text",
        "required": True,
        "selector": "#roll-number",
    },
    {
        "ref": "el_pw",
        "tag": "input",
        "accessible_name": "Portal password",
        "visible_label": "Portal password",
        "input_type": "password",
        "is_sensitive": True,
        "selector": "#password",
    },
    {
        "ref": "el_2",
        "tag": "button",
        "role": "button",
        "accessible_name": "Submit Application",
        "visible_label": "Submit Application",
        "selector": "button#submit",
    },
]


def fake_plan(intent, snapshot):
    return {
        "task": "Scholarship form",
        "risk": "high",
        "steps": [
            {
                "step_id": 1,
                "target_ref": "el_1",
                "target_selector": "#roll-number",
                "action_type": "fill_field",
                "instruction": "Enter your roll number.",
                "short_caption": "Roll number",
                "completion_signal": "input_change",
            },
            {
                "step_id": 2,
                "target_ref": "el_2",
                "target_selector": "button#submit",
                "action_type": "submit_form",
                "instruction": "Press Submit yourself.",
                "short_caption": "Submit",
                "requires_confirmation": False,
            },
        ],
    }


def make_plan(client, monkeypatch, planner=fake_plan):
    monkeypatch.setattr(main, "generate_plan", planner)
    resp = client.post(
        "/plan", json={"intent": "submit my form", "page_snapshot": SNAPSHOT}
    )
    assert resp.status_code == 200
    return resp.json()


def test_health():
    with TestClient(main.app) as client:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["service"] == "ghost-hands-backend"
        assert "gemini_configured" in body
        assert "accessibility_modes" in body
        assert "large_target" in body["accessibility_modes"]


def test_plan_ok_and_submit_gated(monkeypatch):
    with TestClient(main.app) as client:
        plan = make_plan(client, monkeypatch)
        assert plan["status"] == "ok" and plan["source"] == "gemini"
        assert plan["total_steps"] == 2 and plan["risk"] == "high"
        assert plan["steps"][0]["target_selector"] == "#roll-number"
        assert plan["steps"][1]["requires_confirmation"] is True
        assert plan["steps"][1]["target_selector"] == "button#submit"


def test_sensitive_elements_never_reach_llm(monkeypatch):
    seen = {}

    def spy(intent, snapshot):
        seen["refs"] = [e["ref"] for e in snapshot]
        return fake_plan(intent, snapshot)

    with TestClient(main.app) as client:
        make_plan(client, monkeypatch, spy)
    assert "el_pw" not in seen["refs"]


def test_fallback_when_planner_crashes(monkeypatch):
    def boom(intent, snapshot):
        raise RuntimeError("api down")

    with TestClient(main.app) as client:
        plan = make_plan(client, monkeypatch, boom)
        assert plan["status"] == "fallback" and plan["source"] == "fallback_rules"
        assert [s["target_ref"] for s in plan["steps"]] == ["el_1", "el_2"]
        assert all(s["target_ref"] != "el_pw" for s in plan["steps"])
        assert plan["steps"][0]["target_selector"] == "#roll-number"


def test_second_call_served_from_cache(monkeypatch):
    calls = {"n": 0}

    def counting(intent, snapshot):
        calls["n"] += 1
        return fake_plan(intent, snapshot)

    with TestClient(main.app) as client:
        make_plan(client, monkeypatch, counting)
        second = make_plan(client, monkeypatch, counting)
    assert calls["n"] == 1 and second["source"] == "cache"


def test_force_fallback_skips_gemini(monkeypatch):
    monkeypatch.setenv("FORCE_FALLBACK", "1")

    def must_not_run(intent, snapshot):
        raise AssertionError("Gemini should not be called")

    with TestClient(main.app) as client:
        plan = make_plan(client, monkeypatch, must_not_run)
    assert plan["source"] == "fallback_rules"


def test_websocket_flow_with_confirmation_gate(monkeypatch):
    with TestClient(main.app) as client:
        plan = make_plan(client, monkeypatch)
        sid = plan["session_id"]
        with client.websocket_connect(f"/ws/{sid}") as ws:
            assert ws.receive_json()["type"] == "CONNECTED"

            ws.send_json({"type": "STEP_COMPLETED", "step_id": 1})
            msg = ws.receive_json()
            assert msg["type"] == "SHOW_STEP" and msg["current_step"] == 2

            # submit step: must be blocked until confirmed
            ws.send_json({"type": "STEP_COMPLETED", "step_id": 2})
            assert ws.receive_json()["type"] == "CONFIRMATION_REQUIRED"

            ws.send_json({"type": "CONFIRM"})
            assert ws.receive_json()["type"] == "CONFIRMED"

            ws.send_json({"type": "STEP_COMPLETED", "step_id": 2})
            done = ws.receive_json()
            assert done["type"] == "TASK_COMPLETE" and done["total_steps"] == 2


def test_stale_step_completed_does_not_double_advance():
    session = create_session("x")
    session.steps = [
        {"step_id": 1, "requires_confirmation": False, "short_caption": "a", "instruction": "a"},
        {"step_id": 2, "requires_confirmation": False, "short_caption": "b", "instruction": "b"},
        {"step_id": 3, "requires_confirmation": False, "short_caption": "c", "instruction": "c"},
    ]
    assert process_command(session, "STEP_COMPLETED", step_id=1)["current_step"] == 2
    again = process_command(session, "STEP_COMPLETED", step_id=1)  # duplicate event
    assert again["type"] == "SHOW_STEP" and again["current_step"] == 2


def test_stop_then_confirm_does_not_crash(monkeypatch):
    with TestClient(main.app) as client:
        sid = make_plan(client, monkeypatch)["session_id"]
        assert client.post(f"/sessions/{sid}/stop").json()["status"] == "stopped"
        with client.websocket_connect(f"/ws/{sid}") as ws:
            ws.receive_json()
            ws.send_json({"type": "CONFIRM"})
            assert ws.receive_json()["type"] == "SESSION_STOPPED"


def test_adaptation_counted_and_validated(monkeypatch):
    with TestClient(main.app) as client:
        sid = make_plan(client, monkeypatch)["session_id"]
        with client.websocket_connect(f"/ws/{sid}") as ws:
            ws.receive_json()
            ws.send_json({"type": "ADAPTATION_REQUESTED", "mode": "large_target"})
            assert ws.receive_json()["type"] == "ADAPTATION_APPLIED"
            ws.send_json({"type": "ADAPTATION_REQUESTED", "mode": "nonsense"})
            assert ws.receive_json()["type"] == "ERROR"


def test_bad_ws_message_returns_error_not_crash(monkeypatch):
    with TestClient(main.app) as client:
        sid = make_plan(client, monkeypatch)["session_id"]
        with client.websocket_connect(f"/ws/{sid}") as ws:
            ws.receive_json()
            ws.send_json({"type": "DROP_TABLES"})
            assert ws.receive_json()["type"] == "ERROR"
            ws.send_json({"type": "REPEAT"})
            assert ws.receive_json()["type"] == "SHOW_STEP"


def test_confirm_endpoint_validates(monkeypatch):
    with TestClient(main.app) as client:
        sid = make_plan(client, monkeypatch)["session_id"]
        ok = client.post(f"/sessions/{sid}/confirm", json={"step_id": 2, "confirmed": True})
        assert ok.status_code == 200
        bad = client.post(f"/sessions/{sid}/confirm", json={"step_id": 99, "confirmed": True})
        assert bad.status_code == 400


def test_rest_command_endpoint(monkeypatch):
    """Test Universal Input Bridge via REST command endpoint."""
    with TestClient(main.app) as client:
        sid = make_plan(client, monkeypatch)["session_id"]

        # NEXT command
        resp = client.post(f"/sessions/{sid}/command", json={"type": "NEXT"})
        assert resp.status_code == 200
        assert resp.json()["type"] == "SHOW_STEP"
        assert resp.json()["current_step"] == 2

        # PREV command
        resp = client.post(f"/sessions/{sid}/command", json={"type": "PREV"})
        assert resp.status_code == 200
        assert resp.json()["type"] == "SHOW_STEP"
        assert resp.json()["current_step"] == 1

        # ADAPTATION_REQUESTED command
        resp = client.post(
            f"/sessions/{sid}/command",
            json={"type": "ADAPTATION_REQUESTED", "mode": "large_target"},
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == "ADAPTATION_APPLIED"

        # STOP command
        resp = client.post(f"/sessions/{sid}/command", json={"type": "STOP"})
        assert resp.status_code == 200
        assert resp.json()["type"] == "SESSION_STOPPED"
