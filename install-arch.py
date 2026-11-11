#!/usr/bin/env python3
"""Download, inject, build and install Forged Unsloth on Arch Linux.

Run as your regular user. Only package-manager operations use sudo.
The per-user backend is provisioned with upstream's --local installer so it
matches the newly built frontend, rather than patching an older wheel in place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
UPSTREAM = "https://github.com/unslothai/unsloth.git"
TESTED_REF = "e4ef63188151e0229a78dcd40e6ff401e3d81d07"
BUILD_DEPS = ["base-devel", "git", "python", "nodejs", "npm", "rust", "pkgconf",
              "webkit2gtk-4.1", "gtk3", "libsoup3", "openssl", "librsvg", "libappindicator",
              "curl", "wget", "file", "cmake", "patchelf", "ttf-dejavu"]
CONTAINER_DEPS = ["podman", "passt", "fuse-overlayfs"]
RUNTIME_DEPS = ["cairo", "curl", "dbus", "gcc-libs", "gdk-pixbuf2", "glib2", "glibc",
                "gtk3", "hicolor-icon-theme", "libsoup3", "webkit2gtk-4.1", "xdg-utils"]
IGNORE = shutil.ignore_patterns(".git", ".agents", ".aws", ".codex", "node_modules", "target",
                                ".forged-build", "__pycache__", "*.pyc", ".pytest_cache",
                                ".venv*", "artifacts")


class InstallError(RuntimeError):
    pass


def run(arguments, *, cwd=None, env=None, capture=False):
    arguments = [str(value) for value in arguments]
    print("+ " + shlex.join(arguments), flush=True)
    return subprocess.run(arguments, cwd=cwd, env=env, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fetch_source(destination, repository=UPSTREAM, ref="main"):
    """Explicit fetch records the exact commit and also supports tags and commit IDs."""
    if not repository or repository.startswith("-") or not ref or ref.startswith("-"):
        raise InstallError("Repository and ref must be nonempty and cannot begin with '-'")
    run(["git", "init", destination])
    run(["git", "remote", "add", "origin", repository], cwd=destination)
    run(["git", "fetch", "--depth", "1", "origin", ref], cwd=destination)
    commit = run(["git", "rev-parse", "FETCH_HEAD"], cwd=destination, capture=True).stdout.strip()
    run(["git", "-c", "advice.detachedHead=false", "checkout", "--detach", commit], cwd=destination)
    return commit


def stage_source(data_dir, *, repository=UPSTREAM, ref="main", local_source=None):
    sources = data_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = Path(tempfile.mkdtemp(prefix="unsloth-", dir=sources))
    if local_source is None:
        commit = fetch_source(destination, repository, ref)
    else:
        local_source = local_source.resolve(strict=True)
        if destination == local_source or local_source in destination.parents:
            raise InstallError("The installer data directory cannot be inside --source")
        shutil.copytree(local_source, destination, dirs_exist_ok=True, ignore=IGNORE)
        if shutil.which("git"):
            result = subprocess.run(["git", "-C", str(local_source), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=False)
            commit = result.stdout.strip() if result.returncode == 0 else "local"
        else:
            commit = "local"
    for required in ("install.sh", "pyproject.toml", "studio/src-tauri/Cargo.toml", "studio/frontend/package-lock.json"):
        if not (destination / required).is_file():
            raise InstallError(f"Source is missing {required}; retained at {destination}")
    plugin_target = destination / "plugins/forged-unsloth"
    if plugin_target.resolve() != PLUGIN.resolve():
        shutil.copytree(PLUGIN, plugin_target, dirs_exist_ok=True, ignore=IGNORE)
    script = plugin_target / "install.py"
    try:
        run([sys.executable, script, "install", "--studio", destination / "studio"])
        run([sys.executable, script, "verify", "--studio", destination / "studio"])
    except subprocess.CalledProcessError as error:
        raise InstallError(
            f"The injection does not match upstream {commit}. No application files have been replaced. "
            f"Source retained at {destination}. Try --ref {TESTED_REF}, or update the injection anchors."
        ) from error
    provenance = {"repository": str(local_source) if local_source else repository,
                  "requested_ref": ref, "commit": commit,
                  "plugin_version": "0.1.0", "source": str(destination),
                  "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_json(destination / "forged-build.json", provenance)
    return destination, provenance


def missing_dependencies(packages):
    result = subprocess.run(["pacman", "-T", *packages], capture_output=True, text=True, check=False)
    if result.returncode not in (0, 127):
        raise InstallError(f"pacman could not check dependencies: {result.stderr.strip()}")
    missing = result.stdout.splitlines()
    if any(package not in packages for package in missing):
        raise InstallError("Unexpected pacman dependency response")
    return missing


def ensure_dependencies(containers):
    packages = list(dict.fromkeys(BUILD_DEPS + RUNTIME_DEPS + (CONTAINER_DEPS if containers else [])))
    missing = missing_dependencies(packages)
    if missing:
        run(["sudo", "pacman", "-S", "--needed", *missing])
    for command in ("git", "python", "npm", "cargo", "makepkg", "pkg-config"):
        if not shutil.which(command):
            raise InstallError(f"Required command is unavailable after dependency installation: {command}")


def running_unsloth(proc_root=Path("/proc")):
    """Identify only this user's Studio desktop and API processes, with PID identities."""
    found = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            if entry.stat().st_uid != os.getuid() or pid == os.getpid():
                continue
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
            arguments = [item.decode(errors="replace") for item in arguments if item]
            if not arguments:
                continue
            executable = Path(arguments[0]).name
            command = " ".join(arguments)
            desktop = executable in {"unsloth-studio", "forged-unsloth"}
            python = executable.startswith("python")
            backend = python and any(item.endswith("studio/backend/run.py") for item in arguments[1:])
            backend = backend or (python and "studio.backend.run" in arguments)
            cli = executable == "unsloth" or (python and len(arguments) > 1 and Path(arguments[1]).name == "unsloth")
            backend = backend or (cli and "studio" in arguments[1:])
            backend = backend or (python and any(item.endswith("forged-unsloth/serve.py") for item in arguments[1:]) and "--studio" in arguments)
            if (python or executable == "uvicorn") and "uvicorn" in command and "main:app" in arguments:
                cwd = (entry / "cwd").resolve()
                backend = backend or (cwd.name == "backend" and cwd.parent.name == "studio")
            if desktop or backend:
                # /proc stat's second field may contain spaces and parentheses.
                started = (entry / "stat").read_text().rsplit(")", 1)[1].split()[19]
                found.append({"pid": pid, "started": started,
                              "name": "Unsloth Desktop" if desktop else "Studio API"})
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return found


def confirm_stop(prompt):
    try:
        with open("/dev/tty", "r") as reader, open("/dev/tty", "w") as writer:
            writer.write(prompt + " [y/N]: ")
            writer.flush()
            return reader.readline().strip().lower() in {"y", "yes"}
    except OSError as error:
        raise InstallError("Unsloth is running. Close it first, or rerun the installer in a terminal to approve stopping it.") from error


def ensure_stopped(*, non_interactive=False):
    processes = running_unsloth()
    if not processes:
        return
    print("Detected running Unsloth processes:", flush=True)
    for process in processes:
        print(f"  {process['name']} (PID {process['pid']})", flush=True)
    if non_interactive:
        raise InstallError("Unsloth is running. Close it before using --non-interactive; no processes were stopped.")
    if not confirm_stop("Stop these Unsloth processes before continuing? Active generations will stop"):
        raise InstallError("Installation cancelled; Unsloth was left running.")
    # Recheck identity immediately before signaling, so an exited/reused PID is skipped.
    current = {(item["pid"], item["started"]) for item in running_unsloth()}
    for process in processes:
        if (process["pid"], process["started"]) in current:
            try:
                os.kill(process["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 15
    while running_unsloth():
        if time.monotonic() >= deadline:
            raise InstallError("Unsloth did not stop within 15 seconds. Close the remaining processes before retrying; installation has not continued.")
        time.sleep(0.2)
    print("Unsloth stopped. Continuing installation.", flush=True)


def backup_user_state(data_dir, studio_home):
    """Use SQLite's online backup API, including any committed WAL data."""
    backups = data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="before-install-", dir=backups))
    for folder in (studio_home, studio_home / "auth"):
        if not folder.exists():
            continue
        for database in folder.glob("*.db"):
            relative = database.relative_to(studio_home)
            target = backup / "studio-state" / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            uri = database.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as original, sqlite3.connect(target) as saved:
                original.backup(saved)
            target.chmod(0o600)
    auth = studio_home / "auth"
    if auth.is_dir():
        for item in auth.iterdir():
            if item.is_file() and not item.name.endswith((".db", "-wal", "-shm")):
                target = backup / "studio-state/auth" / item.name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(item, target)
                target.chmod(0o600)
    if Path("/usr/bin/unsloth-studio").is_file():
        shutil.copy2("/usr/bin/unsloth-studio", backup / "previous-unsloth-studio")
    atomic_json(backup / "backup.json", {"studio_home": str(studio_home),
                "note": "State and executable backup; not a complete Python-environment backup. Upstream handles installer rollback."})
    print(f"State backup: {backup}", flush=True)
    return backup


def install_agents_template(data_dir, studio_home):
    """Explicitly replace the user's memory with the bundled refined template, preserving a backup."""
    template = PLUGIN / "templates/AGENTS.md"
    if not template.is_file():
        raise InstallError("The refined AGENTS.md template is missing; regenerate the standalone installer")
    target = studio_home / "AGENTS.md"
    if target.is_symlink():
        raise InstallError("AGENTS.md is a symlink; choose --no-overwrite-agents or replace the link explicitly")
    studio_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    backup = None
    if target.exists():
        directory = data_dir / "backups"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        saved = Path(tempfile.mkdtemp(prefix="agents-before-install-", dir=directory))
        backup = saved / "AGENTS.md"
        shutil.copy2(target, backup)
        backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(prefix=".AGENTS-", dir=studio_home)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(template.read_bytes())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Refined memory installed: {target}", flush=True)
    if backup:
        print(f"Previous memory preserved: {backup}", flush=True)
    return {"path": str(target), "backup": str(backup) if backup else None}


def customize(args, ask=None):
    """A TTY-backed wizard also works when the bootstrap is piped into bash."""
    terminal = None
    terminal_output = None
    if ask is None:
        try:
            terminal = open("/dev/tty", "r")
            terminal_output = open("/dev/tty", "w")
        except OSError as error:
            raise InstallError("Customization needs a terminal. Use --non-interactive with explicit options instead.") from error
        def ask(prompt):
            terminal_output.write(prompt)
            terminal_output.flush()
            value = terminal.readline()
            if not value:
                raise InstallError("Customization cancelled: terminal input ended")
            return value.strip()
    def yes(prompt, default):
        while True:
            answer = ask(prompt + (" [Y/n]: " if default else " [y/N]: ")).strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
    try:
        print("Forged Unsloth installation customization\n"
              "1. Full desktop + matching backend (replaces unsloth-bin and updates the per-user runtime)\n"
              "2. Backend only (keeps the installed desktop package)\n"
              "3. Build package only (does not replace the app, backend or user memory)\n"
              "4. Download and inject only (does not replace installed application files)\n"
              "5. Refined AGENTS.md only (does not download or install Unsloth)", flush=True)
        mode = ask("Choose install mode [1]: ").strip() or "1"
        if mode not in {"1", "2", "3", "4", "5"}:
            raise InstallError("Choose a mode from 1 to 5")
        args.backend_only = mode == "2"
        args.build_only = mode == "3"
        args.fetch_only = mode == "4"
        args.agents_only = mode == "5"
        if mode != "5":
            if yes("Download official upstream Unsloth instead of using a local checkout?", args.source is None):
                args.source = None
                args.repository = UPSTREAM
                args.ref = ask(f"Unsloth branch, tag or commit [{args.ref}]: ").strip() or args.ref
            else:
                value = ask(f"Local Unsloth checkout{f' [{args.source}]' if args.source else ''}: ").strip()
                if value:
                    args.source = Path(value).expanduser()
                if args.source is None:
                    raise InstallError("A local checkout path is required")
        if mode in {"1", "2"}:
            args.skip_container = not yes("Install/configure rootless Podman and download Debian?", not args.skip_container)
        if mode in {"1", "2", "5"}:
            args.overwrite_agents = yes("Overwrite ~/.unsloth/studio/AGENTS.md with the refined template (backup first)?", args.overwrite_agents)
        else:
            args.overwrite_agents = False
        print("\nSelected overwrites:", flush=True)
        print("  Desktop package: " + ("replace with Forged Unsloth" if mode == "1" else "keep"))
        print("  Per-user Python backend: " + ("update from injected source" if mode in {"1", "2"} else "keep"))
        print("  AGENTS.md: " + ("replace with refined template, preserving a backup" if args.overwrite_agents else "keep"))
        print("  Chats, models, API keys and workspace folder configuration: preserve")
        if not yes("Continue with these choices?", True):
            raise InstallError("Installation cancelled before making changes")
        return args
    finally:
        if terminal:
            terminal.close()
        if terminal_output:
            terminal_output.close()


def build_desktop(source):
    run(["npm", "ci", "--ignore-scripts"], cwd=source / "studio")
    run(["npm", "ci", "--ignore-scripts"], cwd=source / "studio/frontend")
    config = source / ".forged-build/tauri.json"
    atomic_json(config, {"bundle": {"createUpdaterArtifacts": False}})
    env = os.environ.copy()
    # Keep output in this source snapshot, independent of a caller's build-tree settings.
    env["CARGO_TARGET_DIR"] = str(source / ".forged-build/target")
    env.pop("TAURI_SIGNING_PRIVATE_KEY", None)
    env.pop("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", None)
    run([str(source / "studio/node_modules/.bin/tauri"), "build", "--no-bundle", "--config", config],
        cwd=source / "studio", env=env)
    binary = source / ".forged-build/target/release/unsloth-studio"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise InstallError(f"Tauri did not produce the expected executable: {binary}")
    return binary


def shell_array(values):
    return "(" + " ".join(shlex.quote(value) for value in values) + ")"


def package_desktop(source, binary, provenance):
    directory = source / ".forged-build/arch"
    directory.mkdir(parents=True, exist_ok=True)
    files = {"unsloth-studio": binary, "install.sh": source / "install.sh",
             "studio-license": source / "studio/LICENSE.AGPL-3.0",
             "library-license": source / "LICENSE", "forged-build.json": source / "forged-build.json"}
    icon_root = source / "studio/src-tauri/icons/linux"
    for size in (32, 128):
        icon = icon_root / f"{size}x{size}.png"
        if not icon.is_file():
            raise InstallError(f"Missing desktop icon: {icon}")
        files[f"icon-{size}.png"] = icon
    for name, path in files.items():
        shutil.copy2(path, directory / name)
    (directory / "forged-unsloth").write_text('#!/bin/sh\nexec /usr/bin/unsloth-studio "$@"\n')
    (directory / "unsloth-studio.desktop").write_text(
        "[Desktop Entry]\nName=Forged Unsloth\nComment=Unsloth Studio with Forged customizations\n"
        "Exec=unsloth-studio %u\nIcon=unsloth-studio\nType=Application\nTerminal=false\n"
        "Categories=Development;\nStartupWMClass=unsloth-studio\nMimeType=x-scheme-handler/unsloth;\n")
    names = list(files) + ["forged-unsloth", "unsloth-studio.desktop"]
    sums = [hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    commit = provenance["commit"][:12]
    if not all(character.isalnum() for character in commit):
        raise InstallError("Invalid commit for package version")
    arch = platform.machine()
    if arch not in ("x86_64", "aarch64"):
        raise InstallError(f"Unsupported architecture: {arch}")
    pkgbuild = f'''# Generated by Forged Unsloth; build as a regular user.
pkgname=forged-unsloth
pkgver=0.1.0.r{stamp}.g{commit}
pkgrel=1
pkgdesc='Unsloth Studio with Forged Unsloth customization'
arch=('{arch}')
url='https://github.com/unslothai/unsloth'
license=('AGPL-3.0-only' 'Apache-2.0')
depends={shell_array(RUNTIME_DEPS)}
optdepends=('podman: Agentic container tools' 'passt: rootless container networking')
provides=('unsloth' 'unsloth-studio')
conflicts=('unsloth-bin' 'unsloth')
options=('!strip' '!debug')
source={shell_array(names)}
sha256sums={shell_array(sums)}
package() {{
    install -Dm755 "$srcdir/unsloth-studio" "$pkgdir/usr/bin/unsloth-studio"
    install -Dm755 "$srcdir/forged-unsloth" "$pkgdir/usr/bin/forged-unsloth"
    install -Dm755 "$srcdir/install.sh" "$pkgdir/usr/lib/Unsloth/install.sh"
    install -Dm644 "$srcdir/unsloth-studio.desktop" "$pkgdir/usr/share/applications/unsloth-studio.desktop"
    install -Dm644 "$srcdir/icon-32.png" "$pkgdir/usr/share/icons/hicolor/32x32/apps/unsloth-studio.png"
    install -Dm644 "$srcdir/icon-128.png" "$pkgdir/usr/share/icons/hicolor/128x128/apps/unsloth-studio.png"
    install -Dm644 "$srcdir/studio-license" "$pkgdir/usr/share/licenses/$pkgname/studio-license"
    install -Dm644 "$srcdir/library-license" "$pkgdir/usr/share/licenses/$pkgname/library-license"
    install -Dm644 "$srcdir/forged-build.json" "$pkgdir/usr/share/forged-unsloth/build.json"
}}
'''
    (directory / "PKGBUILD").write_text(pkgbuild)
    run(["makepkg", "--cleanbuild", "--force"], cwd=directory)
    packages = list(directory.glob("forged-unsloth-*.pkg.tar.*"))
    packages = [path for path in packages if not path.name.endswith(".sig")]
    if len(packages) != 1:
        raise InstallError(f"Expected one Arch package in {directory}, found {len(packages)}")
    return packages[0]


def provision_backend(source):
    env = os.environ.copy()
    env["UNSLOTH_SKIP_AUTOSTART"] = "1"
    env.pop("UNSLOTH_STUDIO_HOME", None)
    env.pop("STUDIO_HOME", None)
    env.pop("UNSLOTH_HOME", None)
    # --tauri uses the standard desktop runtime and skips unnecessary frontend rebuilds.
    run(["bash", source / "install.sh", "--local", "--tauri"], cwd=source, env=env)
    python = Path.home() / ".unsloth/studio/unsloth_studio/bin/python"
    if not python.is_file():
        raise InstallError(f"Upstream installer did not create the desktop Python runtime: {python}")
    result = run([python, "-c", "import pathlib,studio; print(pathlib.Path(studio.__file__).resolve().parent)"],
                 cwd=Path("/tmp"), capture=True)
    actual = Path(result.stdout.strip())
    if actual != source / "studio":
        raise InstallError(f"Backend resolves to {actual}, expected {source / 'studio'}. Desktop package has not been installed.")
    return python


def prepare_containers():
    rootless = run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], capture=True).stdout.strip()
    if rootless.lower() != "true":
        raise InstallError("Rootless Podman is required. Configure your user's subordinate UID/GID ranges, or rerun with --skip-container.")
    run(["podman", "pull", "docker.io/library/debian:13-slim"])


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--repository", default=UPSTREAM, help="Upstream Git repository (default: official Unsloth)")
    result.add_argument("--ref", default="main", help="Git branch, tag or commit to fetch (default: main)")
    result.add_argument("--source", type=Path, help="Snapshot an existing checkout instead of downloading")
    result.add_argument("--data-dir", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "forged-unsloth")
    result.add_argument("--customize", action="store_true", help="Choose installation mode and overwrites interactively")
    result.add_argument("--non-interactive", action="store_true", help="Use command-line choices without the customization menu")
    result.add_argument("--overwrite-agents", action=argparse.BooleanOptionalAction, default=True,
                        help="Replace AGENTS.md with the refined template, backing up the old file (default: on for installs)")
    result.add_argument("--agents-only", action="store_true", help="Only install the refined AGENTS.md; do not fetch/build Unsloth")
    result.add_argument("--backend-only", action="store_true", help="Provision the injected backend without replacing the desktop package")
    result.add_argument("--build-only", action="store_true", help="Build the Arch package; do not provision the backend or replace the installed package")
    result.add_argument("--fetch-only", action="store_true", help="Download/snapshot and inject only; do not install dependencies or build")
    result.add_argument("--skip-container", action="store_true", help="Skip Podman installation, validation and image pull")
    result.add_argument("--plan", action="store_true", help="Print the steps without downloading or changing anything")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.plan:
        print("1. Fetch official Unsloth main (or --repository/--ref), or snapshot --source.\n"
              "2. Inject Forged Unsloth; reject incompatible sources before changing the installed app.\n"
              "3. Install missing Arch build dependencies with sudo pacman; build Tauri and a local Arch package.\n"
              "4. Prepare rootless Podman and pull the default image unless --skip-container.\n"
              "5. Back up Studio databases/auth and its executable. Run upstream install.sh --local --tauri as your user.\n"
              "6. Verify the backend source path; install forged-unsloth with sudo pacman -U, replacing unsloth-bin.\n"
              "7. Replace AGENTS.md with the refined template, backing up the old file, unless --no-overwrite-agents.\n"
              "Source snapshots are retained because the Python runtime uses an editable installation.\n"
              "--build-only stops before steps 4–6. --fetch-only stops after step 2.")
        return 0
    if args.customize and not args.non_interactive:
        args = customize(args)
    modes = [args.agents_only, args.backend_only, args.build_only, args.fetch_only]
    if sum(modes) > 1:
        raise InstallError("Choose only one of --agents-only, --backend-only, --build-only and --fetch-only")
    if platform.system() != "Linux" or os.geteuid() == 0:
        raise InstallError("Run this script on Arch Linux as your regular user, not with sudo.")
    data_dir = args.data_dir.expanduser().resolve()
    if args.agents_only:
        if args.overwrite_agents:
            install_agents_template(data_dir, Path.home() / ".unsloth/studio")
        else:
            print("AGENTS.md preserved; no changes requested.")
        return 0
    if not shutil.which("pacman") or not shutil.which("makepkg"):
        raise InstallError("Arch Linux with pacman and makepkg is required. Install pacman/base-devel first.")
    if not args.fetch_only and not args.build_only:
        ensure_stopped(non_interactive=args.non_interactive)
    # Bootstrap Git only when fetching needs it; full dependencies wait until injection validates.
    if args.source is None and not shutil.which("git"):
        if args.fetch_only:
            raise InstallError("Install Git first, or omit --fetch-only to let this installer install it.")
        run(["sudo", "pacman", "-S", "--needed", "git"])
    data_dir = args.data_dir.expanduser().resolve()
    source, provenance = stage_source(data_dir, repository=args.repository, ref=args.ref, local_source=args.source)
    print(f"Pinned upstream commit: {provenance['commit']}\nPersistent source: {source}", flush=True)
    if args.fetch_only:
        return 0
    ensure_dependencies(not args.skip_container and not args.build_only)
    package = None
    if not args.backend_only:
        binary = build_desktop(source)
        package = package_desktop(source, binary, provenance)
        print(f"Arch package: {package}", flush=True)
    if args.build_only:
        print("Build only: per-user backend and installed package have not been changed.")
        return 0
    ensure_stopped(non_interactive=args.non_interactive)
    if not args.skip_container:
        prepare_containers()
    backup = backup_user_state(data_dir, Path.home() / ".unsloth/studio")
    runtime = provision_backend(source)
    run([sys.executable, source / "plugins/forged-unsloth/install.py", "verify", "--studio", source / "studio"])
    # Recheck: do not swap the desktop if it was reopened during provisioning.
    ensure_stopped(non_interactive=args.non_interactive)
    if package is not None:
        run(["sudo", "pacman", "-U", package])
    agents = install_agents_template(data_dir, Path.home() / ".unsloth/studio") if args.overwrite_agents else None
    atomic_json(data_dir / "installation.json", {**provenance, "package": str(package) if package else None,
                "backup": str(backup), "python": str(runtime), "agents": agents})
    print(("\nForged Unsloth installed. Launch: forged-unsloth\n" if package else "\nInjected backend installed. Desktop package preserved; use the injected source/API until you build its matching desktop.\n")
          + f"Keep {source}: the backend imports it.\n"
          "Rerun this installer to fetch and rebuild a newer upstream version.\n"
          "To restore the official desktop package: paru -S unsloth-bin; then use its repair/update flow for the backend.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, subprocess.CalledProcessError, OSError) as error:
        print(f"\nForged Unsloth installation stopped: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        raise SystemExit(1)
