"""Browser smoke test with mocked API; no model or host mutation. Saves a preview."""
import argparse
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--studio", type=Path, required=True, help="Injected Unsloth studio directory")
args = parser.parse_args()
FRONTEND = args.studio.resolve() / "frontend"
if not (FRONTEND / "src/forged-unsloth/forged.tsx").is_file():
    parser.error("Inject Forged Unsloth into --studio before running this preview")
html = FRONTEND / "forged-preview.html"
entry = FRONTEND / "forged-preview.tsx"
workspace = {"enabled": False, "network": False, "image": "docker.io/library/python:3.12-slim", "folders": []}
key = "sk-unsloth-preview-only"


def handler(route):
    global key, workspace
    path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
    if path == "forged/status":
        result = {"config": workspace, "podman_available": False, "mcp_command": "python /preview/forged_unsloth/mcp.py"}
    elif path == "forged/key/rotate":
        key = "sk-unsloth-preview-rotated"
        result = {"api_key": key}
    elif path == "forged/key":
        result = {"api_key": key}
    elif path == "forged/workspace":
        workspace = route.request.post_data_json
        result = workspace
    elif path == "mcp/servers/import":
        result = {"created": [], "skipped": [], "errors": []}
    elif path.startswith("mcp/servers"):
        result = []
    else:
        result = {}
    route.fulfill(json=result)


html.write_text('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="background:#191919;margin:32px"><div id="preview"></div><script type="module" src="/forged-preview.tsx"></script></body></html>')
entry.write_text('import "./src/index.css"; import {createRoot} from "react-dom/client"; import {ForgedRemote} from "./src/forged-unsloth/forged"; import {ForgedAbout} from "./src/forged-unsloth/about"; import {AgenticPill} from "./src/forged-unsloth/agentic"; createRoot(document.getElementById("preview")!).render(<main style={{maxWidth:760,margin:"auto",color:"#efefef",fontFamily:"system-ui"}}><ForgedAbout/><AgenticPill/><ForgedRemote/></main>);')
log = open("/tmp/forged-preview-vite.log", "w")
server = subprocess.Popen(["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5198", "--strictPort"], cwd=FRONTEND, stdout=log, stderr=log)
try:
    for _ in range(100):
        try:
            urlopen("http://127.0.0.1:5198/forged-preview.html", timeout=1).close()
            break
        except OSError:
            if server.poll() is not None:
                raise RuntimeError("Preview server failed; see /tmp/forged-preview-vite.log")
            time.sleep(.2)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1100, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: (errors.append(str(error)), print("BROWSER ERROR:", error, flush=True)))
        page.route("http://127.0.0.1:5198/api/**", handler)
        page.add_init_script('localStorage.setItem("unsloth_auth_token", "preview-ui");')
        page.goto("http://127.0.0.1:5198/forged-preview.html")
        page.wait_for_timeout(1500)
        if errors: raise AssertionError(errors)
        page.get_by_role("button", name="Save workspace", exact=True).wait_for()
        assert page.get_by_test_id("forged-about").is_visible()
        badge = page.get_by_test_id("forged-about").get_by_text("Forged", exact=True)
        assert badge.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(217, 119, 87)"
        page.get_by_role("button", name="Enable Agentic workspace", exact=True).click()
        page.get_by_role("alert").filter(has_text="Install rootless Podman").wait_for()
        page.get_by_role("button", name="Open Automations", exact=True).wait_for()
        page.get_by_role("button", name="Reveal key", exact=True).click()
        page.get_by_label("API key", exact=True).wait_for()
        expect(page.get_by_label("API key", exact=True)).to_have_value(key)
        page.get_by_role("button", name="Reroll key", exact=True).click()
        page.get_by_text("Previous key revoked. Update your clients.", exact=True).wait_for()
        expect(page.get_by_label("API key", exact=True)).to_have_value("sk-unsloth-preview-rotated")
        page.get_by_role("checkbox", name="New Chat", exact=True).uncheck()
        assert page.evaluate('document.documentElement.hasAttribute("data-forged-hide-newchat")')
        page.get_by_role("checkbox", name="New Chat", exact=True).check()
        assert not page.evaluate('document.documentElement.hasAttribute("data-forged-hide-newchat")')
        page.get_by_role("button", name="Save workspace", exact=True).click()
        page.get_by_text("Workspace saved.", exact=False).wait_for()
        assert not workspace["enabled"]
        page.get_by_label("MCP configuration", exact=False).fill('{"mcpServers": {}}')
        page.get_by_role("button", name="Import integrations", exact=True).click()
        page.get_by_text("Imported 0; skipped 0.", exact=False).wait_for()
        page.get_by_role("alert").filter(has_text="Install rootless Podman").click()
        page.get_by_role("button", name="Hide", exact=True).click()
        assert page.get_by_label("API key", exact=True).get_attribute("type") == "password"
        assert not errors, errors
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "forged-unsloth.png"), full_page=True)
        print("Browser smoke passed: About, orange badge, key reveal/rotation, navigation, workspace save, MCP import; no browser errors")
        browser.close()
finally:
    server.terminate()
    server.wait(timeout=10)
    log.close()
    html.unlink(missing_ok=True)
    entry.unlink(missing_ok=True)
