"""Private stdio MCP, registered only in the GUI's integrations list."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP
from forged_unsloth import config, container

mcp = FastMCP("Forged Unsloth Workspace", instructions=(
    "Use forged_terminal for any language, shell command, application, or file operation. "
    "This is a persistent Debian sandbox in rootless Podman, not a Python-only runtime. "
    "You are root inside the container, not on the host. Use apt-get and other installers to download "
    "and install tools, runtimes and dependencies as needed. Network is enabled by default; "
    "the owner can choose offline mode. Installed packages and /root persist between commands. "
    "/workspace is persistent scratch storage. User-connected host folders are writable at "
    "/projects/0, /projects/1, etc.; changes there directly change the user's files. "
    "Each command starts a new shell, so use files rather than relying on shell variables. "
    "Interrupt stops all running sandbox commands/services, but preserves files and installed apps. "
    "The next command can restart the sandbox. Never claim a file was attached or tested without tool evidence."
))


@mcp.tool()
def forged_terminal(command: str, timeout: int = 300) -> dict:
    """Run any Debian shell command; install packages, download tools, build and test apps."""
    return container.execute(command, timeout)


@mcp.tool()
def forged_python(code: str) -> dict:
    """Optional Python helper. Install python3 through forged_terminal if it is not present."""
    return container.python(code)


@mcp.tool()
def forged_workspace() -> dict:
    """List available container paths and workspace policy."""
    settings = config.read()
    return {"enabled": settings["enabled"], "network": settings["network"],
            "distribution": "Debian", "persistent": True, "package_manager": "apt-get",
            "workspace": "/workspace", "projects": [f"/projects/{i}" for i in range(len(settings["folders"]))]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
