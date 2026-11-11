"""Private stdio MCP, registered only in the GUI's integrations list."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP
from forged_unsloth import config, container

mcp = FastMCP("Forged Unsloth Workspace", instructions=(
    "Use forged_terminal and forged_python for coding and file operations. "
    "Commands run in rootless Podman, never on the host. /workspace persists between calls. "
    "User-connected host folders are mounted writable at /projects/0, /projects/1, etc. "
    "Changes there directly change the user's files. Each call starts a fresh container; "
    "shell state and installed packages do not persist. Network is disabled unless the owner enables it."
))


@mcp.tool()
def forged_terminal(command: str, timeout: int = 120) -> dict:
    """Run a shell command in the GUI's Podman workspace. Read and edit files here."""
    return container.execute(command, timeout)


@mcp.tool()
def forged_python(code: str) -> dict:
    """Run Python in the Podman workspace, with access to connected folders."""
    return container.python(code)


@mcp.tool()
def forged_workspace() -> dict:
    """List available container paths and workspace policy."""
    settings = config.read()
    return {"enabled": settings["enabled"], "network": settings["network"],
            "workspace": "/workspace", "projects": [f"/projects/{i}" for i in range(len(settings["folders"]))]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
