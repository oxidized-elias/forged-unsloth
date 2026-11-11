# Forged Unsloth

A plugin and installer for customizing [Unsloth Studio](https://github.com/unslothai/unsloth). This repository contains **only Forged Unsloth**: injection code, settings UI, installers, tests, and a preview. The installer downloads Unsloth separately.

## Install on Arch Linux

Close Unsloth Desktop and any separately running Studio API server, then run as your normal user:

```bash
git clone https://github.com/oxidized-elias/forged-unsloth.git
cd forged-unsloth
python3 forged-unsloth-installer.py
```

The standalone Python script embeds the plugin, so you can also download or copy that single file and run it from another directory. **Do not launch the script with sudo.** It invokes sudo when pacman needs permission.

By default, it:

1. Fetches official Unsloth `main` and records the exact commit.
2. Injects Forged Unsloth, rejecting incompatible source layouts before replacing the installed app.
3. Installs missing Arch build dependencies and builds the desktop with the customized frontend.
4. Installs Podman dependencies, checks rootless operation, and pulls the default Python image.
5. Backs up Studio databases, authentication files, and the current desktop executable.
6. Uses Unsloth's local installer to provision the matching Python backend.
7. Installs a local `forged-unsloth` Arch package that replaces `unsloth-bin`.

This installs the full desktop app and its backend. Launch **Forged Unsloth** from the application menu, or run:

```bash
forged-unsloth
```

The process downloads npm, Cargo, and Python dependencies and may take substantial time and disk space. A full native build and privileged installation have not yet been verified end to end. Real Podman execution also remains unverified; container command construction and access restrictions have automated tests.

## Features

- **Appearance:** Claude-inspired charcoal `#191919`, warm orange `#D97757`, and off-white `#EFEFEF`.
- **Branding:** an orange **Forged** badge replaces the top-left Beta badge; About includes plugin version and feature information.
- **Integrations:** import LM Studio / Cursor style `mcp.json` through Studio's existing MCP system, with connection management, OAuth, and tool selection.
- **Remote & LAN:** listener controls and native API keys generated on server startup, with reveal, copy, and reroll. Reroll revokes the previous plugin key.
- **Agentic:** a composer control beside Search and Tools that enables Podman workspace tools for GUI chats.
- **Connected folders:** user-selected writable mounts let the model edit the original project files. External API clients retain their own tool environments and are not offered the Podman integration.
- **Skills:** uses Studio's existing Skills support alongside the container MCP integration.
- **Automations:** exposes Studio's scheduler for predefined prompts, chosen models, timezone-aware one-time or recurring schedules, notifications, and run history.
- **Navigation:** individual visibility switches for main sidebar entries, including New Chat and Automations.

Automations require Studio to be running when they are due. They do not wake a sleeping or powered-off computer.

<details>
<summary>Settings preview — mocked API, not a live model session</summary>

![Forged Unsloth settings preview](artifacts/forged-unsloth.png)

</details>

## Installer options

```bash
# Print the steps without downloading or installing.
python3 forged-unsloth-installer.py --plan

# Download Unsloth and inject only.
python3 forged-unsloth-installer.py --fetch-only

# Build an Arch package without provisioning the backend or replacing the app.
python3 forged-unsloth-installer.py --build-only

# Pin the Unsloth revision used for the initial plugin implementation.
python3 forged-unsloth-installer.py --ref e4ef63188151e0229a78dcd40e6ff401e3d81d07

# Snapshot an existing checkout, including its uncommitted changes.
python3 forged-unsloth-installer.py --source /path/to/unsloth

# Install without configuring or downloading Podman components.
python3 forged-unsloth-installer.py --skip-container
```

`--ref` accepts a branch, tag, or commit. Default `main` may change; compatibility checks stop on unsupported injection points, with no automatic fallback to a different revision. The initial revision has been checked against the injection anchors; that is not a guarantee of a complete native installation.

Sources, package artifacts, provenance, and backups are retained below `${XDG_DATA_HOME:-$HOME/.local/share}/forged-unsloth`. Override this with `--data-dir`.

**Keep the active source snapshot.** The backend imports it as an editable installation. The installer upgrades the current user's backend under `~/.unsloth/studio`, where existing models and conversations remain. Backups cover SQLite state, auth files, and the executable, not every Python environment or model. Unsloth's installer retains its own rollback behavior.

Rootless Podman must be usable by your account. The installer checks it but does not edit subordinate UID/GID mappings automatically. If configuration is missing, configure rootless Podman or use `--skip-container` and enable it later.

## Podman workspace

Open **Forged Unsloth → Agent workspace**, select an image, and enter absolute project-folder paths. Each command uses a fresh rootless container:

| Path | Purpose |
| --- | --- |
| `/workspace` | Persistent scratch files in the plugin's configuration directory |
| `/projects/0`, `/projects/1`, … | Writable mounts of the folders you connected |
| `/tmp` | Temporary container storage |

Changes in `/projects/*` directly change the original files. Shell state and packages installed during a command do not persist; use a custom prebuilt image for additional tools.

Containers default to no network, a read-only image filesystem, dropped capabilities, no added privileges, and CPU, memory, process, duration, and returned-output limits. No host engine socket or host environment secrets are mounted. Images must be pulled before tool execution; tool calls never pull them automatically.

Turn on **Agentic** in the composer to enable the integration and MCP tool calling. Workspace enablement and mount policy are installation-wide; the chat's MCP switch controls whether the tools are offered in that chat. GUI native terminal, Python, and edit-file calls are refused while the workspace policy is enabled and direct the model to the container tools. External clients and other accounts are not offered the workspace integration.

A model/provider capable of Studio-managed MCP tool calls is required.

## API access

External OpenAI-compatible clients use `/v1` with the generated Bearer key. OpenCode provider and Claude Code MCP examples are available in the settings panel.

To start the backend directly without `unsloth start`, use the installed Studio Python environment and the **injected source path** recorded in `installation.json`:

```bash
~/.unsloth/studio/unsloth_studio/bin/python serve.py \
  --studio /path/to/injected/unsloth/studio \
  --host 127.0.0.1 --port 8888
```

This runs a foreground API service. Use a process manager if you want it kept running. The direct launcher binds the address you supply; dynamic LAN/tunnel controls require the normal Studio launcher to configure their launch policy.

The startup key is saved at `~/.config/forged-unsloth/api-key.json` with mode `0600`. `FORGED_UNSLOTH_HOME` overrides that configuration directory. The key is not printed to logs. Restart or reroll revokes the previous plugin key, so update client configurations afterward. Other existing Studio credentials retain their native behavior.

```bash
curl http://127.0.0.1:8888/v1/models \
  -H 'Authorization: Bearer YOUR_STARTUP_KEY'
```

For Claude Code, enable Studio MCP in Settings → API or launch with `UNSLOTH_STUDIO_ENABLE_MCP=1`, then configure `.mcp.json`:

```json
{
  "mcpServers": {
    "forged-unsloth": {
      "type": "http",
      "url": "http://127.0.0.1:8888/mcp",
      "headers": { "Authorization": "Bearer YOUR_STARTUP_KEY" }
    }
  }
}
```

Studio's OpenAI-compatible API does not become an Anthropic Messages API. Claude Code accesses Studio through MCP and retains its own filesystem and command tools. Owner configuration actions continue to require the owner's regular UI session.

## Source injection and removal

To inject a matching Unsloth checkout manually:

```bash
python3 install.py install --studio /path/to/unsloth/studio
cd /path/to/unsloth/studio/frontend
npm ci --ignore-scripts
npm run build
```

The injector validates all anchors before modifying source, records installed hashes, and supports verification and removal:

```bash
python3 install.py verify --studio /path/to/unsloth/studio
python3 install.py uninstall --studio /path/to/unsloth/studio
```

Rebuild the frontend after removal. For a packaged native desktop, also rebuild its executable because its frontend is embedded. Uninstall removes the exact injected fragments and preserves unrelated source edits. It preserves plugin settings, workspace files, and API-key records; revoke unused keys in Studio's API settings.

Injection survives restarts. Application updates may replace it; rerun the installer to fetch, validate, and rebuild against a newer source revision. To return to the official Arch desktop package, install `unsloth-bin` through your AUR helper and run its backend repair/reinstallation. Reinstalling only the desktop package does not undo the editable backend.

## Development and tests

The repository contains no Unsloth source tree or history. Plugin and installer tests run independently:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests cover authorization, key rotation, external-client isolation, mount validation, install/uninstall round trips, local Git download and injection, Arch package generation, SQLite WAL backups, and the standalone installer's reproducible payload. Native compilation, privileged installation, and real model/container execution are outside this test suite.

The browser preview requires an injected Unsloth checkout with frontend dependencies, Python Playwright, and system Chromium:

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python tests/preview.py --studio /path/to/unsloth/studio
```

It uses mocked APIs and saves `artifacts/forged-unsloth.png`.

After editing the injection or plugin files, regenerate the standalone installer:

```bash
python3 build-installer.py
```

`install-arch.py` is the readable implementation embedded by `build-installer.py` into `forged-unsloth-installer.py`.

## License and references

Forged Unsloth is distributed under [AGPL-3.0-only](LICENSE). It is an independent customization; upstream Unsloth retains its own copyrights and licenses.

Reference designs: [LM Studio MCP](https://lmstudio.ai/docs/app/mcp), [Claude sandboxing](https://www.anthropic.com/research/claude-code-sandboxing), [Codex cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments). Desktop build references: [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/) and [distribution](https://v2.tauri.app/distribute/).
