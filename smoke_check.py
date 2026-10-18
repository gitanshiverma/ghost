"""End-to-end check against a RUNNING server: python backend/tools/smoke_check.py [http://localhost:8000]

Validates:
1. /health
2. /plan (credential firewall, LLM/fallback planning, target_selector resolution)
3. Universal Input Bridge (REST command + WebSocket)
4. Confirmation gate for sensitive/submit action
5. Task completion lifecycle
"""
import json
import sys
from pathlib import Path

import httpx
from websockets.sync.client import connect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
WS_BASE = BASE.replace("http", "ws", 1)
snapshot = json.loads((Path(__file__).parent / "sample_snapshot.json").read_text())


def fail(msg: str):
    print(f"FAIL: {msg}")
    sys.exit(1)


print(f"Connecting to Ghost Hands backend at {BASE}...")
health = httpx.get(f"{BASE}/health", timeout=5).json()
print("1. Health check:", health)
if health.get("status") != "ok":
    fail("Health status is not ok")

print("\n2. Requesting plan for scholarship form...")
plan = httpx.post(
    f"{BASE}/plan",
    json={"intent": "Help me fill and submit the scholarship form", "page_snapshot": snapshot},
    timeout=40,
).json()
print(f"Plan response: status={plan['status']} source={plan.get('source')} steps={plan['total_steps']} risk={plan['risk']}")
for s in plan["steps"]:
    print(f"  Step {s['step_id']}: [{s['action_type']}] selector={s.get('target_selector')} confirm={s['requires_confirmation']}")
    print(f"    Instruction: {s['instruction']}")

if plan["total_steps"] == 0:
    fail(f"No steps returned: {plan.get('message')}")

# Credential firewall check
bad_refs = {"el_4", "el_5"}  # password and OTP
if any(s["target_ref"] in bad_refs for s in plan["steps"]):
    fail("A sensitive element (password/OTP) was targeted by the planner!")
print("OK: Credential firewall verified - password/OTP never targeted")

sid = plan["session_id"]

# Test REST Command endpoint (Universal Input Bridge)
print("\n3. Testing Universal Input Bridge (REST command endpoint)...")
cmd_resp = httpx.post(
    f"{BASE}/sessions/{sid}/command",
    json={"type": "ADAPTATION_REQUESTED", "mode": "large_target"},
).json()
print("Adaptation response:", cmd_resp)
if cmd_resp.get("type") != "ADAPTATION_APPLIED":
    fail("Failed to apply large_target adaptation via REST command")

print("\n4. Testing WebSocket step progression and confirmation gate...")
with connect(f"{WS_BASE}/ws/{sid}") as ws:
    connected = json.loads(ws.recv())
    print("WebSocket connected:", connected["type"])

    done = False
    for step in plan["steps"]:
        if step["requires_confirmation"]:
            # Attempt to complete without confirming - must be blocked by the gate!
            ws.send(json.dumps({"type": "STEP_COMPLETED", "step_id": step["step_id"]}))
            reply = json.loads(ws.recv())
            if reply["type"] != "CONFIRMATION_REQUIRED":
                fail(f"Confirmation gate did not block sensitive step: {reply}")
            print(f"OK: Step {step['step_id']} safely held at confirmation gate")

            # Explicit user confirmation
            ws.send(json.dumps({"type": "CONFIRM"}))
            confirm_reply = json.loads(ws.recv())
            if confirm_reply["type"] != "CONFIRMED":
                fail(f"Expected CONFIRMED, got {confirm_reply}")
            print("  User confirmed ->", confirm_reply["type"])

        # Complete the step
        ws.send(json.dumps({"type": "STEP_COMPLETED", "step_id": step["step_id"]}))
        reply = json.loads(ws.recv())
        print(f"  Step {step['step_id']} completed ->", reply["type"])
        done = reply["type"] == "TASK_COMPLETE"

    if not done:
        fail("Workflow did not reach TASK_COMPLETE")

print("\n5. All checks PASSED! Ghost Hands backend is running cleanly.")
