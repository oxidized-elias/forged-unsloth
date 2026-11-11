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
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
UPSTREAM = "https://github.com/unslothai/unsloth.git"
TESTED_REF = "e4ef63188151e0229a78dcd40e6ff401e3d81d07"
BUILD_DEPS = ["base-devel", "git", "python", "nodejs", "npm", "rust", "pkgconf",
              "webkit2gtk-4.1", "gtk3", "libsoup3", "openssl", "librsvg", "libappindicator",
              "curl", "wget", "file", "cmake", "patchelf", "ttf-dejavu"]
CONTAINER_DEPS = ["podman", "slirp4netns", "fuse-overlayfs"]
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


def ensure_stopped():
    result = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "unsloth-studio"],
                            capture_output=True, check=False)
    if result.returncode == 0:
        raise InstallError("Close Unsloth Studio before installing. No running application will be killed.")
    # A separately launched API backend also owns the runtime being changed.
    root = Path("/proc")
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid() or int(entry.name) == os.getpid():
                continue
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if ("python" in command and ("studio/backend/run.py" in command or "uvicorn main:app" in command)) or "unsloth studio -" in command:
                raise InstallError("A Studio API backend is running. Stop it before installing.")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


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
optdepends=('podman: Agentic container tools' 'slirp4netns: rootless container networking')
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
    run(["podman", "pull", "docker.io/library/python:3.12-slim"])


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--repository", default=UPSTREAM, help="Upstream Git repository (default: official Unsloth)")
    result.add_argument("--ref", default="main", help="Git branch, tag or commit to fetch (default: main)")
    result.add_argument("--source", type=Path, help="Snapshot an existing checkout instead of downloading")
    result.add_argument("--data-dir", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "forged-unsloth")
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
              "Source snapshots are retained because the Python runtime uses an editable installation.\n"
              "--build-only stops before steps 4–6. --fetch-only stops after step 2.")
        return 0
    if platform.system() != "Linux" or os.geteuid() == 0:
        raise InstallError("Run this script on Arch Linux as your regular user, not with sudo.")
    if not shutil.which("pacman") or not shutil.which("makepkg"):
        raise InstallError("Arch Linux with pacman and makepkg is required. Install pacman/base-devel first.")
    if not args.fetch_only and not args.build_only:
        ensure_stopped()
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
    binary = build_desktop(source)
    package = package_desktop(source, binary, provenance)
    print(f"Arch package: {package}", flush=True)
    if args.build_only:
        print("Build only: per-user backend and installed package have not been changed.")
        return 0
    ensure_stopped()
    if not args.skip_container:
        prepare_containers()
    backup = backup_user_state(data_dir, Path.home() / ".unsloth/studio")
    runtime = provision_backend(source)
    run([sys.executable, source / "plugins/forged-unsloth/install.py", "verify", "--studio", source / "studio"])
    # Recheck: do not swap the desktop if it was reopened during provisioning.
    ensure_stopped()
    run(["sudo", "pacman", "-U", package])
    atomic_json(data_dir / "installation.json", {**provenance, "package": str(package),
                "backup": str(backup), "python": str(runtime)})
    print("\nForged Unsloth installed. Launch: forged-unsloth\n"
          f"Keep {source}: the backend imports it.\n"
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
