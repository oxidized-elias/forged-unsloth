"""Persistent, rootless Debian workspaces and cross-process command interruption."""
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from . import config

MAX_OUTPUT = 128 * 1024
COMMAND_LOCK = threading.Lock()
OWNER_LABEL = "io.forged.unsloth.workspace"


def _read(name, fallback=None):
    path = config.home() / name
    return json.loads(path.read_text()) if path.exists() else fallback


def _epoch():
    return _read("interrupt.json", {}).get("generation", "initial")


def _owner():
    return hashlib.sha256(str(config.home().resolve()).encode()).hexdigest()[:16]


def _name(settings):
    policy = {key: settings[key] for key in ("image", "folders", "network")}
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:16]
    return f"forged-unsloth-{_owner()}-{digest}"


@contextlib.contextmanager
def _locked():
    # MCP and HTTP run in different processes. Interrupt deliberately never takes this lock.
    import fcntl
    config.home().mkdir(parents=True, exist_ok=True, mode=0o700)
    with COMMAND_LOCK, (config.home() / "workspace.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _podman(*args, timeout=60, check=True):
    result = subprocess.run(["podman", *args], capture_output=True, text=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise ValueError((result.stderr or result.stdout or "Podman operation failed").strip())
    return result


def _rootless():
    if not shutil.which("podman"):
        raise ValueError("Podman is not installed")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ValueError("Run Studio as your regular user; rootful Podman is disabled")
    if _podman("info", "--format", "{{.Host.Security.Rootless}}").stdout.strip().lower() != "true":
        raise ValueError("A rootless Podman engine is required")


def _inspect(name):
    result = _podman("container", "inspect", name, check=False)
    if result.returncode:
        return None
    record = json.loads(result.stdout)[0]
    if record.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != _owner():
        raise ValueError("The workspace container name belongs to a different owner")
    return record


def _stop(name):
    if not isinstance(name, str) or not re.fullmatch(r"forged-unsloth-" + _owner() + r"-[0-9a-f]{16}", name):
        raise ValueError("Invalid workspace container")
    record = _inspect(name)
    if record and record.get("State", {}).get("Running"):
        _podman("stop", "--time", "1", name, timeout=20)


def argv(settings, name):
    # Container UID 0 maps to the ordinary host user in rootless Podman. It can run apt
    # and installers inside Debian without gaining host root or mounting host secrets.
    command = ["podman", "create", "--name", name, "--pull=missing",
               "--user=0:0", "--cap-drop=ALL", "--security-opt=no-new-privileges",
               "--pids-limit=512", "--memory=4g", "--cpus=2",
               "--network=" + ("pasta:--no-map-gw" if settings["network"] else "none"),
               "--workdir=/workspace", "--env=HOME=/root", "--env=DEBIAN_FRONTEND=noninteractive",
               "--label", f"{OWNER_LABEL}={_owner()}", "--entrypoint=/bin/sh"]
    for capability in ("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "SETGID", "SETUID", "SETFCAP"):
        command.append("--cap-add=" + capability)
    scratch = config.home() / "workspace"
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    command += ["--mount", f"type=bind,src={scratch},dst=/workspace"]
    for index, folder in enumerate(settings["folders"]):
        command += ["--mount", f"type=bind,src={folder},dst=/projects/{index}"]
    return command + [settings["image"], "-c", "exec sleep infinity"]


def _ensure(settings, name):
    record = _inspect(name)
    if record is None:
        result = subprocess.run(argv(settings, name), capture_output=True, text=True, timeout=600, check=False)
        if result.returncode:
            raise ValueError((result.stderr or result.stdout).strip())
    if record is None or not record.get("State", {}).get("Running"):
        _podman("start", name)


def interrupt():
    # Invalidate admitted/queued commands before stopping; no wait on the execution lock.
    config.write({"generation": uuid.uuid4().hex}, "interrupt.json")
    state = _read("sandbox.json", {})
    name = state.get("container")
    if name:
        _rootless()
        _stop(name)
    return {"interrupted": True, "container": name, "preserved": True}


def configure(settings):
    previous = config.read()
    if previous != settings:
        interrupt()
    config.write(settings)
    return settings


def status():
    state = _read("sandbox.json", {})
    return {"container": state.get("container"), "persistent": True,
            "command_running": bool(state.get("active")) and state.get("generation") == _epoch()}


def _cancelled():
    return {"exit_code": 130, "timed_out": False, "interrupted": True,
            "output": "Workspace command interrupted.", "truncated": False}


def execute(command, timeout=300):
    if not config.read()["enabled"]:
        raise ValueError("Enable the Podman workspace in Forged Unsloth first")
    if not isinstance(command, str) or not command.strip() or len(command) > 100_000:
        raise ValueError("Provide a nonempty command of at most 100000 characters")
    timeout = max(1, min(int(timeout), 3600))
    admitted = _epoch()
    with _locked():
        if _epoch() != admitted:
            return _cancelled()
        settings = config.validate(config.read())
        if not settings["enabled"]:
            return _cancelled()
        _rootless()
        name = _name(settings)
        previous = _read("sandbox.json", {})
        if previous.get("container") and previous["container"] != name:
            _stop(previous["container"])
        state = {"container": name, "active": True, "generation": admitted}
        config.write(state, "sandbox.json")
        output = bytearray()
        total = 0
        timed_out = False
        process = None
        reader = None
        try:
            _ensure(settings, name)
            if _epoch() != admitted:
                _stop(name)
                return _cancelled()
            process = subprocess.Popen(["podman", "exec", "--user", "0:0", "--workdir", "/workspace",
                                        name, "/bin/bash", "-lc", command],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            def drain():
                nonlocal total
                with process.stdout:
                    while chunk := process.stdout.read(8192):
                        total += len(chunk)
                        room = MAX_OUTPUT - len(output)
                        if room > 0:
                            output.extend(chunk[:room])

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if _epoch() != admitted or time.monotonic() >= deadline:
                    timed_out = _epoch() == admitted
                    _stop(name)  # Stops the process tree inside the container, retaining its layer.
                    break
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if reader is not None:
                reader.join(timeout=5)
            config.write({**state, "active": False}, "sandbox.json")
        interrupted = _epoch() != admitted
        return {"exit_code": 130 if interrupted else 124 if timed_out else process.returncode,
                "timed_out": timed_out, "interrupted": interrupted,
                "output": output.decode("utf-8", errors="replace"), "truncated": total > MAX_OUTPUT}


def python(code):
    return execute("command -v python3 >/dev/null || { echo 'Install Python with: apt-get update && apt-get install -y python3' >&2; exit 127; }; python3 -c " + shlex.quote(code))
