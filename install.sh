#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Run from GitHub: curl -fsSL https://raw.githubusercontent.com/oxidized-elias/forged-unsloth/main/install.sh | bash
set -euo pipefail

forged_install_main() {
    if [[ "${EUID}" -eq 0 ]]; then
        printf '%s\n' 'Run this installer as your regular user, not with sudo.' >&2
        return 1
    fi
    command -v python3 >/dev/null || { printf '%s\n' 'Python 3 is required. On Arch: sudo pacman -S python' >&2; return 1; }
    command -v curl >/dev/null || { printf '%s\n' 'curl is required. On Arch: sudo pacman -S curl' >&2; return 1; }
    FORGED_INSTALLER_TMP=$(mktemp -d -t forged-installer.XXXXXXXX)
    trap 'rm -rf -- "$FORGED_INSTALLER_TMP"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    local plugin_ref="${FORGED_UNSLOTH_PLUGIN_REF:-main}"
    local script_url="https://raw.githubusercontent.com/oxidized-elias/forged-unsloth/${plugin_ref}/forged-unsloth-installer.py"
    curl --fail --show-error --silent --location "$script_url" --output "$FORGED_INSTALLER_TMP/installer.py"
    local -a menu=(--customize)
    local option
    for option in "$@"; do
        case "$option" in --non-interactive|--plan|--help|-h) menu=();; esac
    done
    # The Python menu reads /dev/tty, so it never consumes the piped shell script.
    python3 "$FORGED_INSTALLER_TMP/installer.py" "${menu[@]}" "$@"
}

forged_install_main "$@"
