#!/usr/bin/env python3
"""Version-checked source injection; reversible without overwriting unrelated edits."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def patches():
    return {
        "backend/main.py": [("def setup_frontend(\n", "# Forged Unsloth injection\nfrom forged_unsloth.plugin import install as _install_forged_unsloth\n_install_forged_unsloth(app)\n\n\ndef setup_frontend(\n")],
        "backend/core/inference/tools.py": [
            ('    require_tool_access(disable_sandbox = disable_sandbox)\n    logger.info(f"execute_tool: name={name}, session_id={session_id}, timeout={timeout}")', '    require_tool_access(disable_sandbox = disable_sandbox)\n    from forged_unsloth.guard import intercept as _forged_intercept\n    _forged_result = _forged_intercept(name, arguments)\n    if _forged_result is not None:\n        return _forged_result\n    logger.info(f"execute_tool: name={name}, session_id={session_id}, timeout={timeout}")'),
            ('    keyless = keyless_caller(current_account())', '    from forged_unsloth.guard import visible_server as _forged_visible_server\n    servers = [server for server in servers if _forged_visible_server(server)]\n    keyless = keyless_caller(current_account())'),
            ('        server_id = server["id"]\n        display = server.get("display_name") or server_id', '        from forged_unsloth.guard import visible_server as _forged_visible_server\n        if not _forged_visible_server(server):\n            return "Forged Unsloth Podman tools are available only in the owner GUI."\n        server_id = server["id"]\n        display = server.get("display_name") or server_id'),
        ],
        "frontend/src/main.tsx": [
            ('import "./index.css";', 'import "./index.css";\nimport "./forged-unsloth/forged";\nimport { waitForStartup } from "./forged-unsloth/browser-reliability";'),
            ('  Promise.all([\n    localeInitialization,\n    interfaceScaleInitialization,\n    chatPageAtStart,\n  ]).then(renderApp);', '  void waitForStartup([\n    localeInitialization,\n    interfaceScaleInitialization,\n    chatPageAtStart,\n  ]).then(renderApp);'),
        ],
        "frontend/src/features/browser/native-view.ts": [
            ('let listening = false;\n\nfunction listenOnce(): void {\n  if (listening) return;\n  listening = true;\n  void import("@tauri-apps/api/event").then(({ listen }) =>\n    listen<NativeEvent>(EVENT, (event) => onNativeEvent(event.payload)),\n  );\n}', 'import { retryableOnce } from "@/forged-unsloth/browser-reliability";\n\nconst listenOnce = retryableOnce(async () => {\n  const { listen } = await import("@tauri-apps/api/event");\n  await listen<NativeEvent>(EVENT, (event) => onNativeEvent(event.payload));\n});'),
            ('  listenOnce();\n  if (!watchingBackground)', '  void listenOnce().catch(() => undefined);\n  if (!watchingBackground)'),
            ('export function startNativeViews(): () => void {\n  listenOnce();', 'export function startNativeViews(): () => void {\n  void listenOnce().catch(() => undefined);'),
            ('  const resumed = resume.get(tabId);\n  try {\n    if (views.has(tabId)) {', '  const resumed = resume.get(tabId);\n  const openingGeneration = generation;\n  try {\n    await listenOnce();\n    if (openingGeneration !== generation) return;\n    if (views.has(tabId)) {'),
            ('  try {\n    openedTabs.add(tabId);', '  try {\n    await listenOnce();\n    if (stale()) return;\n    openedTabs.add(tabId);'),
        ],
        "frontend/src/features/browser/agent/executor.ts": [
            ('  if (!chatOnScreen(context)) store.updateTab(tabId, { loading: true });', '  if (!chatOnScreen(context) || useNativeBrowser.getState().enabled) store.updateTab(tabId, { loading: true });'),
        ],
        "frontend/src/features/browser/api.ts": [
            ('export class BrowserFetchError extends Error {', 'import { retryBrowserRead } from "@/forged-unsloth/browser-reliability";\n\nexport class BrowserFetchError extends Error {'),
            ('  constructor(message: string, botCheck: boolean) {\n    super(message);\n    this.botCheck = botCheck;\n  }', '  readonly status: number;\n  constructor(message: string, botCheck: boolean, status = 0) {\n    super(message);\n    this.botCheck = botCheck;\n    this.status = status;\n  }'),
            ('export async function fetchBrowserPage(request: BrowserRequest, signal: AbortSignal): Promise<BrowserPage> {', 'export async function fetchBrowserPage(request: BrowserRequest, signal: AbortSignal): Promise<BrowserPage> {\n  return retryBrowserRead(() => fetchBrowserPageOnce(request, signal), signal, request.method !== "POST");\n}\n\nasync function fetchBrowserPageOnce(request: BrowserRequest, signal: AbortSignal): Promise<BrowserPage> {'),
            ('throw new BrowserFetchError(message.replace(/^Failed to fetch URL:\\s*/, ""), botCheck);', 'throw new BrowserFetchError(message.replace(/^Failed to fetch URL:\\s*/, ""), botCheck, response.status);'),
        ],
        "frontend/src/features/browser/tab-view.tsx": [
            ('      .then((page) => {\n        cachePage(entry, page);', '      .then((page) => {\n        if (controller.signal.aborted) return;\n        cachePage(entry, page);'),
        ],
        "frontend/src/features/settings/tabs/remote-lan-tab.tsx": [("export function RemoteLanTab() {", "export { ForgedRemote as RemoteLanTab } from \"@/forged-unsloth/forged\";\n\nexport function LegacyRemoteLanTab() {")],
        "frontend/src/features/settings/stores/appearance-custom-store.ts": [
            ('  tasks: false,', '  tasks: true,'),
        ],
        "frontend/src/features/chat/stores/chat-runtime-store.ts": [
            ('  autoTitle: false,', '  autoTitle: true,'),
        ],
        "frontend/src/features/chat/shared-composer.tsx": [
            ('  function stop() {', '  function stop() {\n    void interruptAgenticWorkspace().catch(() => undefined);'),
            ('import { AiSpeechIcon, BookmarkIcon, FileSearchIcon } from "@/lib/hugeicons-derived";', 'import { AiSpeechIcon, BookmarkIcon, FileSearchIcon } from "@/lib/hugeicons-derived";\nimport { AgenticPill, interruptAgenticWorkspace } from "@/forged-unsloth/agentic";'),
            ('            <BrowserAgentPill glyph={(icon) => <PillGlyph>{icon}</PillGlyph>} />', '            <AgenticPill onStopGeneration={stop} />\n            <BrowserAgentPill glyph={(icon) => <PillGlyph>{icon}</PillGlyph>} />'),
        ],
        "frontend/src/features/scheduled-tasks/tasks-page.tsx": [
            ('{t("scheduledTasks.title")}', 'Automations'),
            ('{t("scheduledTasks.newTask")}', 'New automation'),
            ('{t("scheduledTasks.description")}', 'Run a predefined prompt with your chosen model at a specific time, once or on a repeating schedule. Studio must be running when the automation is due.'),
        ],
        "frontend/src/features/settings/tabs/about-tab.tsx": [
            ('export function AboutTab() {', 'import { ForgedAbout } from "@/forged-unsloth/about";\n\nexport function AboutTab() {'),
            ('      <StudioVersionSection desktopAppVersion={desktopAppVersion} />', '      <ForgedAbout />\n\n      <StudioVersionSection desktopAppVersion={desktopAppVersion} />'),
        ],
        "frontend/src/app/auth-guards.ts": [
            ('async function hasActiveSession(): Promise<boolean> {', 'import { getAuthToken } from "@/features/auth/session";\n\nasync function hasActiveSession(): Promise<boolean> {'),
            ('  if (await hasActiveSession()) {\n    // Reconcile', '  if (await hasActiveSession()) {\n    if (getAuthToken()?.startsWith("sk-unsloth-")) return;\n    // Reconcile'),
        ],
        "frontend/src/components/app-sidebar.tsx": [
            ('{t("shell.beta")}', 'Forged'),
            ('label: t("shell.navigation.tasks"),', 'label: "Automations",'),
            ('className="nav-badge ml-0.5', 'className="forged-brand-badge nav-badge ml-0.5'),
            ("export function AppSidebar() {", "import { useForgedHidden } from \"@/forged-unsloth/navigation\";\n\nexport function AppSidebar() {\n  const forgedHidden = useForgedHidden();"),
            ("{inlineNavIds.map((id) => {", "{inlineNavIds.filter((id) => !forgedHidden.includes(id)).map((id) => {"),
            ("{overflowNavIds.map((id) => {", "{overflowNavIds.filter((id) => !forgedHidden.includes(id)).map((id) => {"),
            ("              icon={PencilEdit02Icon}\n", "              testId=\"forged-new-chat\"\n              icon={PencilEdit02Icon}\n"),
        ],
    }


def install(studio):
    manifest_path = studio / ".forged-unsloth.json"
    if manifest_path.exists():
        verify(studio)
        print("Forged Unsloth is already installed")
        return
    changes = {}
    # Validate all anchors before the first mutation.
    for name, replacements in patches().items():
        path = studio / name
        content = path.read_text()
        for old, new in replacements:
            if content.count(old) != 1:
                raise RuntimeError(f"Unsupported Studio version: {name}: expected one injection anchor, found {content.count(old)}")
            content = content.replace(old, new, 1)
        changes[name] = content
    destinations = {"backend/forged_unsloth": ROOT / "backend", "frontend/src/forged-unsloth": ROOT / "frontend"}
    for name in destinations:
        if (studio / name).exists():
            raise RuntimeError(f"Refusing to overwrite {name}")
    applied = []
    created = []
    try:
        for name, source in destinations.items():
            shutil.copytree(source, studio / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            created.append(name)
        for name, content in changes.items():
            (studio / name).write_text(content)
            applied.append(name)
        files = {name: hashlib.sha256((studio / name).read_bytes()).hexdigest() for name in changes}
        for directory in destinations:
            for path in (studio / directory).rglob("*"):
                if path.is_file():
                    files[str(path.relative_to(studio))] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps({"name": "Forged Unsloth", "version": "0.1.0", "files": files, "directories": list(destinations)}, indent=2))
    except Exception:
        for name in reversed(applied):
            content = (studio / name).read_text()
            for old, new in reversed(patches()[name]):
                content = content.replace(new, old, 1)
            (studio / name).write_text(content)
        for name in created:
            shutil.rmtree(studio / name)
        raise
    print(f"Injected Forged Unsloth into {studio}. Build the frontend before launching.")


def verify(studio):
    manifest = json.loads((studio / ".forged-unsloth.json").read_text())
    for name, digest in manifest["files"].items():
        path = studio / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Studio changed since injection: {name}. Reinstall against the updated application.")
    print("Forged Unsloth injection verified")


def uninstall(studio):
    # Preserve subsequent edits to the host files; remove only our exact fragments.
    manifest_path = studio / ".forged-unsloth.json"
    manifest = json.loads(manifest_path.read_text())
    changes = {}
    for name, replacements in patches().items():
        content = (studio / name).read_text()
        for old, new in reversed(replacements):
            if content.count(new) != 1:
                raise RuntimeError(f"Injection was changed in {name}; refusing to overwrite it")
            content = content.replace(new, old, 1)
        changes[name] = content
    for directory in manifest["directories"]:
        for path in (studio / directory).rglob("*"):
            name = str(path.relative_to(studio))
            if path.is_file() and path.suffix != ".pyc" and manifest["files"].get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
                raise RuntimeError(f"Plugin file modified: {name}; preserve your edits before uninstalling")
    for name, content in changes.items():
        (studio / name).write_text(content)
    for directory in manifest["directories"]:
        shutil.rmtree(studio / directory)
    manifest_path.unlink()
    print("Injection removed. Rebuild the frontend. User settings and keys are preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "verify", "uninstall"], nargs="?", default="install")
    parser.add_argument("--studio", type=Path, required=True, help="Path to the Unsloth studio directory to inject, verify or uninstall")
    args = parser.parse_args()
    {"install": install, "verify": verify, "uninstall": uninstall}[args.action](args.studio.resolve())
