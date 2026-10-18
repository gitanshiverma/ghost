import os

# Must be set before the app is imported.
os.environ["FORCE_FALLBACK"] = "0"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAN_CACHE_FILE", str(tmp_path / "plans.json"))
    monkeypatch.setenv("FORCE_FALLBACK", "0")
