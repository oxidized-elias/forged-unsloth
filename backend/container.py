"""Rootless Podman tool execution. The host never interprets the model's shell text."""
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from . import config

MAX_OUTPUT = 128 * 1024


def argv(settings, name):
    command = ["podman", "run", "--rm", "--name", name, "--pull=never",
               "--userns=keep-id", "--cap-drop=ALL", "--security-opt=no-new-privileges",
               "--read-only", "--pids-limit=256", "--memory=2g", "--cpus=2",
               "--network=" + ("slirp4netns" if settings["network"] else "none"),
               "--tmpfs=/tmp:rw,nosuid,nodev,size=256m", "--workdir=/workspace",
               "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1"]
    scratch = config.home() / "workspace"
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    command += ["--mount", f"type=bind,src={scratch},dst=/workspace"]
    for index, folder in enumerate(settings["folders"]):
        command += ["--mount", f"type=bind,src={folder},dst=/projects/{index}"]
    return command + [settings["image"]]


def execute(command, timeout=120):
    settings = config.validate(config.read())
    if not settings["enabled"]:
        raise ValueError("Enable the Podman workspace in Forged Unsloth first")
    if not shutil.which("podman"):
        raise ValueError("Podman is not installed")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ValueError("Run Studio as your regular user; rootful Podman is disabled")
    if not isinstance(command, str) or not command.strip() or len(command) > 100_000:
        raise ValueError("Provide a nonempty command of at most 100000 characters")
    timeout = max(1, min(int(timeout), 300))
    name = "forged-unsloth-" + uuid.uuid4().hex
    output = bytearray()
    total = 0
    process = subprocess.Popen(argv(settings, name) + ["/bin/sh", "-lc", command],
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
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Also stop detached descendants in the container when the shell exits.
        try:
            subprocess.run(["podman", "rm", "--force", name], capture_output=True, timeout=15, check=False)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)
            reader.join(timeout=5)
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "output": output.decode("utf-8", errors="replace"), "truncated": total > MAX_OUTPUT}



def python(code):
    import shlex
    return execute("python3 -c " + shlex.quote(code))
