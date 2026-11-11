import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("forged_unsloth", ROOT / "backend/__init__.py",
                                             submodule_search_locations=[str(ROOT / "backend")])
package = importlib.util.module_from_spec(spec)
sys.modules["forged_unsloth"] = package
spec.loader.exec_module(package)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGED_UNSLOTH_HOME", str(tmp_path / "private"))
