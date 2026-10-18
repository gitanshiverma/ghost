"""Tiny disk cache of known-good Gemini plans. Cached plans are ALWAYS re-validated against the
current snapshot before use, so the cache can never bypass the policy engine."""
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Optional

_lock = threading.Lock()


def _path() -> Path:
    return Path(os.getenv("PLAN_CACHE_FILE", ".plan_cache.json"))


def make_key(intent: str, snapshot: list[dict]) -> str:
    shape = [
        (e.get("ref"), e.get("tag"), e.get("input_type"),
         e.get("accessible_name"), e.get("visible_label"))
        for e in snapshot
    ]
    blob = json.dumps([intent.strip().lower(), shape], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:
        return {}


def get(key: str) -> Optional[dict]:
    with _lock:
        return _load().get(key)


def put(key: str, plan: dict) -> None:
    with _lock:
        data = _load()
        data[key] = plan
        try:
            _path().write_text(json.dumps(data))
        except OSError:
            pass
