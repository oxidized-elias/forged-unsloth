"""Opt-in real Debian downloads, non-Python apps, persistent installs and interruption."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from forged_unsloth import config, container

pytestmark = pytest.mark.skipif(os.environ.get("FORGED_PODMAN_TEST") != "1",
                                reason="Set FORGED_PODMAN_TEST=1 to run real rootless Podman integration")


def test_debian_apps_downloads_and_cross_process_interruption(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config.write({"enabled": True, "image": config.DEFAULT_IMAGE, "network": True, "folders": [str(project)]})
    name = container._name(config.read())
    library = tmp_path / "lib"
    shutil.copytree(Path(container.__file__).parent, library / "forged_unsloth")
    try:
        result = container.execute(". /etc/os-release; test \"$ID\" = debian && apt-get update -qq && apt-get install -y -qq curl jq nodejs ca-certificates", timeout=600)
        assert result["exit_code"] == 0, result
        result = container.execute("node -e \"require('fs').writeFileSync('/projects/0/node-result.txt', 'Node works')\" && curl -fLsS --max-time 30 https://example.com -o /workspace/download.html && jq --version && test -s /workspace/download.html")
        assert result["exit_code"] == 0, result
        assert (project / "node-result.txt").read_text() == "Node works"
        result = container.execute("node --version && test -s /workspace/download.html && printf started > /workspace/installed-marker")
        assert result["exit_code"] == 0, result
        answers = []
        worker = threading.Thread(target=lambda: answers.append(container.execute("touch /workspace/interrupt-ready; sleep 45; echo should-not-complete", timeout=60)))
        worker.start()
        ready = config.home() / "workspace/interrupt-ready"
        deadline = time.monotonic() + 20
        while not ready.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(.1)
        assert ready.exists(), "Sandbox command did not start"
        env = {**os.environ, "PYTHONPATH": str(library)}
        stopped = subprocess.run([sys.executable, "-c", "import json; from forged_unsloth.container import interrupt; print(json.dumps(interrupt()))"], env=env, text=True, capture_output=True, timeout=20)
        assert stopped.returncode == 0, stopped.stderr
        assert json.loads(stopped.stdout)["preserved"]
        worker.join(15)
        assert not worker.is_alive(), "Interrupt did not stop the execution thread"
        assert answers[0]["interrupted"] and answers[0]["exit_code"] == 130
        assert "should-not-complete" not in answers[0]["output"]
        result = container.execute("node --version && test -f /workspace/installed-marker && test -s /workspace/download.html")
        assert result["exit_code"] == 0, result
        assert (project / "node-result.txt").exists()
    finally:
        # Only the container created for this test's unique configuration is removed.
        subprocess.run(["podman", "rm", "--force", name], capture_output=True, timeout=30, check=False)
