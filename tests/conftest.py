import threading
import pytest
from fastapi.testclient import TestClient
from hypanel import store, files, runtime, security
from hypanel.app import app


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(files, "DATA", tmp_path)
    monkeypatch.setattr(runtime, "active", lambda sid: False)
    monkeypatch.setattr(runtime, "container", lambda sid, install=False: None)
    monkeypatch.setattr(runtime, "state", lambda s, metrics=False: dict(s, status="stopped", installed=True, memory_bytes=0, cpu_percent=0))
    monkeypatch.setattr(runtime, "logs", lambda *a: "server output")
    security.failures.clear()
    with TestClient(app) as c:
        yield c, tmp_path


@pytest.fixture
def authenticated(panel):
    c, directory = panel
    r = c.post("/api/setup", json={"username": "admin", "password": "test-password-strong", "setup_key": (directory / "setup.key").read_text()})
    assert r.status_code == 200, r.text
    r = c.post("/api/login", json={"username": "admin", "password": "test-password-strong"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c, directory


@pytest.fixture
def server(authenticated):
    c, directory = authenticated
    r = c.post("/api/servers", json={"name": "Test server", "port": 5520})
    assert r.status_code == 201, r.text
    return c, directory, r.json()["id"]
