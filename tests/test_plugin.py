import contextlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
from forged_unsloth import config, container, guard, plugin


def settings(**overrides):
    return {"enabled": True, "network": False, "image": "docker.io/library/debian:13-slim", "folders": [], **overrides}


def test_mount_validation(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    assert config.validate(settings(folders=[str(folder)]))["folders"] == [str(folder)]
    for path in ["/", str(Path.home()), "relative", str(tmp_path)]:
        with pytest.raises(ValueError):
            config.validate(settings(folders=[path]))
    with pytest.raises(ValueError):
        config.validate(settings(image="--privileged"))
    with pytest.raises(ValueError):
        config.validate(settings(network="false"))


def test_container_argv_and_secrets(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    argv = container.argv(settings(folders=[str(folder)]), "test-name")
    assert "--network=none" in argv
    assert "--cap-drop=ALL" in argv and "--read-only" not in argv
    assert "--user=0:0" in argv and "--cap-add=SETUID" in argv
    assert "--userns=host" not in argv and "--pull=missing" in argv
    assert argv[:2] == ["podman", "create"] and "--rm" not in argv
    assert f"type=bind,src={folder},dst=/projects/0" in argv
    assert not any("socket" in arg or "privileged" in arg for arg in argv)
    assert "--network=pasta:--no-map-gw" in container.argv(settings(network=True), "test")
    config.write(settings())
    assert (config.home() / "config.json").stat().st_mode & 0o777 == 0o600


def test_disabled_workspace_does_not_execute(monkeypatch):
    popen = Mock()
    monkeypatch.setattr(container.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="Enable"):
        container.execute("echo hello")
    popen.assert_not_called()


def fake_modules(monkeypatch):
    records = {}
    def create(username, name):
        number = len(records) + 1
        token = f"sk-unsloth-test{number}"
        records[number] = {"token": token, "active": True}
        return token, {"id": number}
    def revoke(username, number):
        records[number]["active"] = False
    def current(request: Request):
        token = request.headers.get("authorization", "")
        if token == "Bearer owner-ui": return "unsloth"
        if any(item["active"] and token == "Bearer " + item["token"] for item in records.values()): return "unsloth"
        raise HTTPException(401)
    def via_key(request: Request):
        return request.headers.get("authorization") != "Bearer owner-ui"
    auth = types.ModuleType("auth")
    auth.storage = types.SimpleNamespace(DEFAULT_ADMIN_USERNAME="unsloth", create_api_key=create, revoke_api_key=revoke)
    auth.authentication = types.SimpleNamespace(get_current_subject=current, authenticated_via_api_key=via_key)
    utils = types.ModuleType("utils")
    utils.account_context = types.SimpleNamespace(is_owner_context=lambda: True)
    utils.remote_access_settings = types.SimpleNamespace(_admin_password_ready=lambda: False)
    utils.lan_access_settings = types.SimpleNamespace(_admin_password_ready=lambda: False)
    storage = types.ModuleType("storage")
    storage.studio_db = types.SimpleNamespace(upsert_chat_settings_merge=Mock())
    monkeypatch.setitem(sys.modules, "storage", storage)
    monkeypatch.setitem(sys.modules, "storage.studio_db", storage.studio_db)
    for name, value in {"auth": auth, "auth.storage": auth.storage, "auth.authentication": auth.authentication, "utils": utils, "utils.account_context": utils.account_context}.items():
        monkeypatch.setitem(sys.modules, name, value)
    return records


def test_key_rotation_and_owner_boundary(monkeypatch):
    records = fake_modules(monkeypatch)
    app = FastAPI()
    plugin.install(app)
    with TestClient(app) as client:
        assert len(records) == 1  # Generated at startup, before any request.
        assert client.get("/api/forged/status").status_code == 401
        owner = {"Authorization": "Bearer owner-ui"}
        response = client.get("/api/forged/key", headers=owner)
        assert response.headers["cache-control"] == "no-store"
        first = response.json()["api_key"]
        assert client.get("/api/forged/connect", headers={"Authorization": f"Bearer {first}"}).status_code == 200
        assert client.get("/api/forged/status", headers={"Authorization": f"Bearer {first}"}).status_code == 403
        assert client.post("/api/forged/workspace/interrupt", headers={"Authorization": f"Bearer {first}"}).status_code == 403
        assert client.post("/api/forged/workspace/interrupt", headers=owner).json()["preserved"] is True
        assert client.put("/api/forged/workspace", headers={"Authorization": f"Bearer {first}"}, json=settings()).status_code == 403
        second = client.post("/api/forged/key/rotate", headers=owner).json()["api_key"]
        assert first != second and not records[1]["active"]
        assert client.post("/api/forged/key/rotate", headers={"Authorization": f"Bearer {first}"}).status_code == 401
        assert client.put("/api/forged/workspace", headers=owner, json=settings(folders=["/"])).status_code == 400
        assert client.put("/api/forged/workspace", headers=owner, json=settings()).status_code == 200
        assert client.put("/api/forged/workspace", headers=owner, json=settings(network="yes")).status_code == 422
    with TestClient(app):
        assert not records[2]["active"] and records[3]["active"]


def test_gui_guard_does_not_intercept_external_clients(monkeypatch):
    config.write(settings())
    caller = types.SimpleNamespace(via_api_key=False, ui_chat=False, is_owner=True)
    monkeypatch.setitem(sys.modules, "utils.account_context", types.SimpleNamespace(current_account=lambda: caller))
    assert "Host execution is disabled" in guard.intercept("terminal", {})
    caller.via_api_key = True
    assert guard.intercept("terminal", {}) is None
    caller.ui_chat = True
    assert guard.intercept("terminal", {}) is not None
    assert guard.intercept("web_search", {}) is None
    server = {"url": f"python {Path(guard.__file__).with_name('mcp.py')}"}
    assert guard.visible_server(server)
    caller.ui_chat = False
    assert not guard.visible_server(server)
    caller.via_api_key = False
    caller.is_owner = False
    assert not guard.visible_server(server)
    assert guard.visible_server({"url": "https://example.com/mcp"})


def test_installer_roundtrip(tmp_path):
    spec = importlib.util.spec_from_file_location("forged_installer", ROOT / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    originals = {}
    for name, replacements in installer.patches().items():
        # A source-shaped fixture keeps plugin tests independent of an Unsloth checkout.
        # Include unrelated content to verify that uninstall preserves it byte for byte.
        source = "# Existing application content\n" + "\n# Between injection points\n".join(
            old for old, _new in replacements
        ) + "\n# Unrelated trailing content\n"
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        originals[name] = source
    installer.install(tmp_path)
    installer.verify(tmp_path)
    installer.install(tmp_path)
    installer.uninstall(tmp_path)
    for name, source in originals.items():
        assert (tmp_path / name).read_text() == source
    assert not (tmp_path / "backend/forged_unsloth").exists()
    path = tmp_path / "backend/main.py"
    path.write_text("unsupported version")
    with pytest.raises(RuntimeError, match="Unsupported"):
        installer.install(tmp_path)
    assert not (tmp_path / "backend/forged_unsloth").exists()
