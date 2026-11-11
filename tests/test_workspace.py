import io
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from forged_unsloth import config, container, plugin


def test_defaults_and_legacy_python_migration():
    assert config.read()["image"] == "docker.io/library/debian:13-slim"
    assert config.read()["network"] is True
    config.write({"enabled": True, "network": False, "image": "docker.io/library/python:3.12-slim", "folders": []})
    assert config.read()["image"] == config.DEFAULT_IMAGE
    assert config.read()["network"] is True
    config.write({**config.read(), "network": False})
    assert config.read()["network"] is False


def test_automatic_titles_seed_once_and_preserve_later_user_choice(monkeypatch):
    upsert = Mock()
    monkeypatch.setitem(sys.modules, "storage.studio_db", SimpleNamespace(upsert_chat_settings_merge=upsert))
    plugin.initialize_defaults()
    plugin.initialize_defaults()
    upsert.assert_called_once_with({"autoTitle": True})


class Process:
    def __init__(self, engine, args, **kwargs):
        self.engine = engine
        self.args = args
        self.stdout = io.BytesIO(b"completed\n")
        self.returncode = None
        engine.processes.append(self)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.fixture
def engine(monkeypatch):
    result = SimpleNamespace(containers={}, creates=[], processes=[], starts=[], stops=[])
    def podman(*args, **kwargs):
        if args[0] == "info":
            return SimpleNamespace(returncode=0, stdout="true", stderr="")
        name = args[-1]
        if args[:2] == ("container", "inspect"):
            record = result.containers.get(name)
            return SimpleNamespace(returncode=0 if record else 125, stdout=json.dumps([record]) if record else "", stderr="")
        if args[0] == "start":
            result.starts.append(name)
            result.containers[name]["State"]["Running"] = True
        elif args[0] == "stop":
            result.stops.append(name)
            result.containers[name]["State"]["Running"] = False
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    def create(args, **kwargs):
        assert args[:2] == ["podman", "create"]
        name = args[args.index("--name") + 1]
        result.creates.append(args)
        result.containers[name] = {"State": {"Running": False}, "Config": {"Labels": {container.OWNER_LABEL: container._owner()}}}
        return SimpleNamespace(returncode=0, stdout=name, stderr="")
    monkeypatch.setattr(container, "_podman", podman)
    monkeypatch.setattr(container.subprocess, "run", create)
    monkeypatch.setattr(container.subprocess, "Popen", lambda *args, **kwargs: Process(result, *args, **kwargs))
    monkeypatch.setattr(container.shutil, "which", lambda _: "/usr/bin/podman")
    monkeypatch.setattr(container.os, "geteuid", lambda: 1000)
    config.write({**config.read(), "enabled": True})
    return result


def test_commands_reuse_writable_debian_container(engine):
    assert container.execute("apt-get update && apt-get install -y curl")["exit_code"] == 0
    assert container.execute("curl --version")["exit_code"] == 0
    assert len(engine.creates) == 1
    assert len(engine.starts) == 1
    assert len(engine.stops) == 0
    assert all(process.args[:2] == ["podman", "exec"] for process in engine.processes)
    assert engine.processes[0].args[-1] == "apt-get update && apt-get install -y curl"
    assert "--read-only" not in engine.creates[0]
    assert container.status()["command_running"] is False


def test_interrupt_stops_without_removing_and_next_command_restarts(engine):
    container.execute("echo ready")
    response = container.interrupt()
    assert response["preserved"] and response["interrupted"]
    assert len(engine.stops) == 1
    container.execute("echo again")
    assert len(engine.creates) == 1
    assert len(engine.starts) == 2


def test_admitted_command_cancelled_while_queued(engine, monkeypatch):
    entered = threading.Event()
    proceed = threading.Event()
    original = container._locked
    import contextlib
    @contextlib.contextmanager
    def delayed():
        entered.set()
        assert proceed.wait(5)
        with original():
            yield
    monkeypatch.setattr(container, "_locked", delayed)
    result = []
    worker = threading.Thread(target=lambda: result.append(container.execute("must not run")))
    worker.start()
    assert entered.wait(5)
    container.interrupt()
    proceed.set()
    worker.join(5)
    assert not worker.is_alive()
    assert result[0]["interrupted"] and result[0]["exit_code"] == 130
    assert not engine.processes and not engine.creates


def test_timeout_stops_container_and_preserves_layer(engine, monkeypatch):
    def wait(self, timeout=None):
        if self.returncode is None:
            if self.engine.stops:
                self.returncode = 137
            else:
                raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode
    monkeypatch.setattr(Process, "wait", wait)
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(container.time, "monotonic", lambda: next(ticks))
    result = container.execute("sleep forever", timeout=1)
    assert result["timed_out"] and result["exit_code"] == 124
    assert len(engine.stops) == 1 and len(engine.containers) == 1


def test_owner_labels_prevent_stopping_unrelated_containers(engine):
    name = container._name(config.read())
    engine.containers[name] = {"State": {"Running": True}, "Config": {"Labels": {container.OWNER_LABEL: "someone-else"}}}
    config.write({"container": name}, "sandbox.json")
    with pytest.raises(ValueError, match="different owner"):
        container.interrupt()
    assert not engine.stops
