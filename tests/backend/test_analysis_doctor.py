"""Provider health checks reveal booleans, not keys, and never import the SDK."""

import importlib.util
import json
import socket
from dataclasses import asdict

import pytest

from dead_letter.backend import doctor


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_provider_health_check_is_presence_only(monkeypatch, installed, configured):
    key = "PRIVATE_SYNTHETIC_DOCTOR_KEY"
    monkeypatch.setenv("TYPESAFE_API_KEY", key if configured else "  ")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://private-user:secret@invalid.example")
    looked_up = []
    def spec(name):
        looked_up.append(name)
        return object() if installed else None
    def forbidden(*args, **kwargs):
        pytest.fail("provider health check imported SDK or attempted network")
    monkeypatch.setattr(importlib.util, "find_spec", spec)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = doctor.check_typesafe_analysis()
    assert looked_up == ["typesafe_sdk"]
    assert result.status == ("ok" if installed and configured else "skip")
    assert f"installed={str(installed).lower()}" in result.message
    assert f"configured={str(configured).lower()}" in result.message
    assert "not contacted" in result.message
    for secret in (key, "private-user", "invalid.example"):
        assert secret not in json.dumps(asdict(result))


def test_doctor_json_includes_presence_check(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_load_settings_paths", lambda: (None, None))
    monkeypatch.setenv("TYPESAFE_API_KEY", "PRIVATE_SYNTHETIC_DOCTOR_KEY")
    assert doctor.run_doctor(json_output=True) == 0
    output = capsys.readouterr().out
    checks = {item["name"]: item for item in json.loads(output)["checks"]}
    assert "typesafe_analysis" in checks
    assert "PRIVATE_SYNTHETIC_DOCTOR_KEY" not in output
