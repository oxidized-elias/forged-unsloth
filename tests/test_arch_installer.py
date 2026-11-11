"""Test real local Git fetching/injection and packaging without sudo or a native build."""
import importlib.util
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("forged_arch", ROOT / "install-arch.py")
arch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arch)
spec = importlib.util.spec_from_file_location("forged_injector", ROOT / "install.py")
injector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(injector)


@pytest.fixture
def upstream(tmp_path):
    repository = tmp_path / "upstream with spaces"
    repository.mkdir()
    for name, replacements in injector.patches().items():
        path = repository / "studio" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(old for old, new in replacements))
    for name, value in {
        "install.sh": "#!/bin/sh\nexit 0\n",
        "pyproject.toml": "[project]\nname='test'\nversion='0.1.0'\n",
        "studio/src-tauri/Cargo.toml": "[package]\nname='unsloth-studio'\nversion='0.1.0'\n",
        "studio/frontend/package-lock.json": "{}",
        "studio/LICENSE.AGPL-3.0": "studio license",
        "LICENSE": "library license",
        "studio/src-tauri/icons/linux/32x32.png": "32 px test image",
        "studio/src-tauri/icons/linux/128x128.png": "128 px test image",
    }.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    subprocess.run(["git", "init", "--initial-branch=main", repository], check=True, capture_output=True)
    subprocess.run(["git", "-C", repository, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repository, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "Fixture"], check=True, capture_output=True)
    return repository


def test_defaults_pull_official_upstream_and_plan_is_read_only(monkeypatch, capsys):
    args = arch.parser().parse_args([])
    assert args.repository == "https://github.com/unslothai/unsloth.git"
    assert args.ref == "main" and args.source is None
    monkeypatch.setattr(arch, "run", lambda *args, **kwargs: pytest.fail("--plan ran a command"))
    assert arch.main(["--plan"]) == 0
    assert "Fetch official Unsloth" in capsys.readouterr().out


def test_real_fetch_and_inject_leaves_upstream_unchanged(tmp_path, upstream):
    source, provenance = arch.stage_source(tmp_path / "data", repository=str(upstream))
    assert len(provenance["commit"]) == 40
    assert provenance["requested_ref"] == "main"
    assert (source / "studio/.forged-unsloth.json").is_file()
    assert (source / "studio/backend/forged_unsloth/plugin.py").is_file()
    assert (source / "plugins/forged-unsloth/install-arch.py").is_file()
    assert "Forged" in (source / "studio/frontend/src/components/app-sidebar.tsx").read_text()
    assert not (upstream / "studio/.forged-unsloth.json").exists()
    injector.verify(source / "studio")
    assert json.loads((source / "forged-build.json").read_text())["commit"] == provenance["commit"]


def test_local_source_snapshot_preserves_uncommitted_changes(tmp_path, upstream):
    (upstream / "local-note.txt").write_text("uncommitted change")
    source, provenance = arch.stage_source(tmp_path / "data", local_source=upstream)
    assert (source / "local-note.txt").read_text() == "uncommitted change"
    assert not (source / ".git").exists()
    assert len(provenance["commit"]) == 40


def test_incompatible_upstream_stops_before_system_changes(tmp_path, upstream, monkeypatch):
    (upstream / "studio/backend/main.py").write_text("incompatible")
    commands = []
    real_run = arch.run
    def record(arguments, **kwargs):
        commands.append([str(argument) for argument in arguments])
        return real_run(arguments, **kwargs)
    monkeypatch.setattr(arch, "run", record)
    with pytest.raises(arch.InstallError, match="does not match"):
        arch.stage_source(tmp_path / "data", local_source=upstream)
    assert not any(command[0] in ("sudo", "makepkg", "npm") for command in commands)


def test_git_arguments_cannot_be_options(tmp_path):
    with pytest.raises(arch.InstallError):
        arch.fetch_source(tmp_path / "clone", "--upload-pack=anything", "main")
    with pytest.raises(arch.InstallError):
        arch.fetch_source(tmp_path / "clone", arch.UPSTREAM, "--anything")


def test_arch_package_manifest_is_valid_and_preserves_tauri_resource_layout(tmp_path, upstream, monkeypatch):
    source, provenance = arch.stage_source(tmp_path / "data", local_source=upstream)
    binary = tmp_path / "test executable"
    binary.write_bytes(b"fake binary")
    binary.chmod(0o755)
    commands = []
    def make_package(arguments, *, cwd, **kwargs):
        commands.append(arguments)
        subprocess.run(["bash", "-n", cwd / "PKGBUILD"], check=True)
        (cwd / "forged-unsloth-0.1.0-1-x86_64.pkg.tar.zst").write_bytes(b"test package")
    monkeypatch.setattr(arch, "run", make_package)
    package = arch.package_desktop(source, binary, provenance)
    assert package.is_file()
    pkgbuild = (package.parent / "PKGBUILD").read_text()
    assert "conflicts=('unsloth-bin' 'unsloth')" in pkgbuild
    assert '"$pkgdir/usr/lib/Unsloth/install.sh"' in pkgbuild
    assert '"$pkgdir/usr/bin/unsloth-studio"' in pkgbuild
    assert "SKIP" not in pkgbuild
    assert commands == [["makepkg", "--cleanbuild", "--force"]]


def test_sqlite_backup_includes_wal_and_preserves_permissions(tmp_path):
    root = tmp_path / "studio"
    root.mkdir()
    auth = root / "auth"
    auth.mkdir()
    (auth / "secret").write_text("test secret")
    with sqlite3.connect(root / "studio.db") as original:
        original.execute("PRAGMA journal_mode=WAL")
        original.execute("CREATE TABLE example (value TEXT)")
        original.execute("INSERT INTO example VALUES ('committed')")
        original.commit()
        backup = arch.backup_user_state(tmp_path / "data", root)
        saved = backup / "studio-state/studio.db"
        with sqlite3.connect(saved) as copied:
            assert copied.execute("SELECT value FROM example").fetchone()[0] == "committed"
        assert saved.stat().st_mode & 0o777 == 0o600
    assert (backup / "studio-state/auth/secret").read_text() == "test secret"
    assert (backup / "studio-state/auth/secret").stat().st_mode & 0o777 == 0o600


def test_standalone_payload_is_reproducible_and_can_fetch_and_inject(tmp_path, upstream):
    import base64
    import hashlib
    import io
    import zipfile
    spec = importlib.util.spec_from_file_location("forged_builder", ROOT / "build-installer.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    first = tmp_path / "standalone.py"
    second = tmp_path / "standalone-again.py"
    builder.build(first)
    builder.build(second)
    assert first.read_bytes() == second.read_bytes()
    spec = importlib.util.spec_from_file_location("forged_portable", first)
    portable = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(portable)
    raw = base64.b64decode(portable.PAYLOAD)
    assert hashlib.sha256(raw).hexdigest() == portable.SHA256
    with zipfile.ZipFile(io.BytesIO(raw)) as payload:
        assert payload.read("install-arch.py") == (ROOT / "install-arch.py").read_bytes()
        assert payload.read("frontend/agentic.tsx") == (ROOT / "frontend/agentic.tsx").read_bytes()
    result = subprocess.run([os.sys.executable, first, "--plan"], capture_output=True, text=True, check=True)
    assert "Fetch official Unsloth" in result.stdout
    # A local repository exercises the default fetch path without external network access.
    result = subprocess.run([os.sys.executable, first, "--repository", str(upstream), "--data-dir", str(tmp_path / "portable-data"), "--fetch-only"], capture_output=True, text=True, check=True)
    assert "Pinned upstream commit:" in result.stdout
    clones = list((tmp_path / "portable-data/sources").iterdir())
    assert len(clones) == 1
    injector.verify(clones[0] / "studio")


def test_memory_template_replacement_backs_up_original_and_refuses_symlinks(tmp_path):
    studio = tmp_path / 'studio'
    studio.mkdir()
    target = studio / 'AGENTS.md'
    target.write_text('original local instructions')
    target.chmod(0o640)
    receipt = arch.install_agents_template(tmp_path / 'data', studio)
    assert target.read_bytes() == (ROOT / 'templates/AGENTS.md').read_bytes()
    backup = Path(receipt['backup'])
    assert backup.read_text() == 'original local instructions'
    assert backup.stat().st_mode & 0o777 == 0o600
    assert target.stat().st_mode & 0o777 == 0o640
    target.unlink()
    target.symlink_to(backup)
    with pytest.raises(arch.InstallError, match='symlink'):
        arch.install_agents_template(tmp_path / 'data', studio)
    assert backup.read_text() == 'original local instructions'


def test_customization_can_preserve_memory_and_skip_container(capsys):
    answers = iter(['1', '', '', 'n', 'n', 'y'])
    args = arch.customize(arch.parser().parse_args([]), ask=lambda prompt: next(answers))
    assert args.skip_container and not args.overwrite_agents
    assert not any([args.backend_only, args.build_only, args.fetch_only, args.agents_only])
    assert 'AGENTS.md: keep' in capsys.readouterr().out


def test_customization_cancellation_and_memory_only_mode(tmp_path, monkeypatch):
    answers = iter(['5', 'y', 'n'])
    with pytest.raises(arch.InstallError, match='cancelled'):
        arch.customize(arch.parser().parse_args([]), ask=lambda prompt: next(answers))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(arch, 'run', lambda *a, **kw: pytest.fail('Memory-only ran a command'))
    assert arch.main(['--agents-only', '--no-overwrite-agents']) == 0
    assert not (tmp_path / '.unsloth').exists()
    assert arch.main(['--agents-only', '--data-dir', str(tmp_path / 'data')]) == 0
    assert (tmp_path / '.unsloth/studio/AGENTS.md').read_bytes() == (ROOT / 'templates/AGENTS.md').read_bytes()


def test_bootstrap_forwards_choices_and_cleans_download(tmp_path):
    # A fake curl downloads a tiny argument recorder; no network or app install occurs.
    tools = tmp_path / 'bin'
    tools.mkdir()
    fake_curl = tools / 'curl'
    fake_curl.write_text('''#!/usr/bin/env python3
import pathlib,sys
output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
output.write_text("import json,sys; print(json.dumps(sys.argv))")
''')
    fake_curl.chmod(0o755)
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']}
    for options, expected in [([], ['--customize']), (['--non-interactive', '--agents-only', '--no-overwrite-agents'], ['--non-interactive', '--agents-only', '--no-overwrite-agents'])]:
        result = subprocess.run(['bash', ROOT / 'install.sh', *options], env=env, capture_output=True, text=True, check=True)
        arguments = json.loads(result.stdout)
        assert arguments[1:] == expected
        assert not Path(arguments[0]).parent.exists()
