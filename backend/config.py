"""Owner-managed configuration; never accept container options from model arguments."""
import json
import os
import tempfile
import threading
from pathlib import Path

LOCK = threading.RLock()


def home():
    return Path(os.environ.get("FORGED_UNSLOTH_HOME", Path.home() / ".config/forged-unsloth"))


def read():
    with LOCK:
        path = home() / "config.json"
        return json.loads(path.read_text()) if path.exists() else {
            "enabled": False, "image": "docker.io/library/python:3.12-slim",
            "folders": [], "network": False,
        }


def write(value, name="config.json"):
    with LOCK:
        directory = home()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, indent=2)
            os.replace(temporary, directory / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate(value):
    if type(value.get("enabled")) is not bool or type(value.get("network")) is not bool:
        raise ValueError("enabled and network must be booleans")
    image = value.get("image", "")
    if not isinstance(image, str) or not image or image.startswith("-") or any(c.isspace() for c in image):
        raise ValueError("Enter a fully qualified container image")
    folders = value.get("folders")
    if not isinstance(folders, list) or len(folders) > 32:
        raise ValueError("Choose up to 32 folders")
    resolved = []
    for folder in folders:
        if not isinstance(folder, str) or not Path(folder).is_absolute():
            raise ValueError("Folder paths must be absolute")
        path = Path(folder).resolve(strict=True)
        if not path.is_dir() or path == Path(path.anchor) or path == Path.home().resolve():
            raise ValueError("Choose a specific project folder, not a filesystem root or home")
        if any(c in str(path) for c in (",", "\n", "\x00")):
            raise ValueError("Folder paths cannot contain commas, newlines or NUL")
        if path == home().resolve() or path in home().resolve().parents:
            raise ValueError("The plugin credential directory cannot be mounted")
        resolved.append(str(path))
    return {"enabled": value["enabled"], "network": value["network"], "image": image, "folders": list(dict.fromkeys(resolved))}
