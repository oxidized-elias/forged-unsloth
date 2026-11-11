"""Prevent GUI native shell tools from silently escaping the selected Podman workspace."""
from . import config


def intercept(name, arguments):
    if name not in {"terminal", "python", "edit_file"} or not config.read()["enabled"]:
        return None
    from utils.account_context import current_account
    caller = current_account()
    if not caller.is_owner or (caller.via_api_key and not caller.ui_chat):
        return None
    return "Forged Unsloth workspace is enabled. Use forged_terminal or forged_python from the Forged Unsloth Workspace integration for this operation. Host execution is disabled."


def visible_server(server):
    from pathlib import Path
    script = str(Path(__file__).with_name("mcp.py"))
    if script not in server.get("url", ""):
        return True
    from utils.account_context import current_account
    caller = current_account()
    return caller.is_owner and (not caller.via_api_key or caller.ui_chat)
