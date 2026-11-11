"""Small injection surface: native authentication, native MCP, owner-only setup."""
import contextlib
import json
import shlex
import shutil
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import config

KEY_LOCK = threading.RLock()
KEY_NAME = "Forged Unsloth startup key"


def rotate_key():
    from auth.storage import DEFAULT_ADMIN_USERNAME, create_api_key, revoke_api_key
    with KEY_LOCK:
        path = config.home() / "api-key.json"
        previous = json.loads(path.read_text()) if path.exists() else None
        key, record = create_api_key(DEFAULT_ADMIN_USERNAME, KEY_NAME)
        try:
            config.write({"api_key": key, "id": record["id"]}, "api-key.json")
        except Exception:
            revoke_api_key(DEFAULT_ADMIN_USERNAME, record["id"])
            raise
        if previous:
            revoke_api_key(DEFAULT_ADMIN_USERNAME, previous["id"])
        return key


def current_key():
    with KEY_LOCK:
        path = config.home() / "api-key.json"
        return json.loads(path.read_text())["api_key"] if path.exists() else rotate_key()


def install(app):
    if getattr(app.state, "forged_unsloth", False):
        return
    app.state.forged_unsloth = True
    from auth.authentication import authenticated_via_api_key, get_current_subject
    from utils.account_context import is_owner_context

    async def require_owner(subject=Depends(get_current_subject), via_key=Depends(authenticated_via_api_key)):
        if not is_owner_context() or via_key:
            raise HTTPException(403, "Configure Forged Unsloth from the owner's desktop/UI session")
        return subject

    router = APIRouter(prefix="/api/forged", dependencies=[Depends(require_owner)])

    @router.get("/status")
    def status():
        return {"name": "Forged Unsloth", "version": "0.1.0", "config": config.read(),
                "podman_available": shutil.which("podman") is not None,
                "mcp_command": shlex.join([sys.executable, str(Path(__file__).with_name("mcp.py"))]),
                "api_base": "/v1", "mcp_endpoint": "/mcp"}

    @router.get("/key")
    def key():
        return JSONResponse({"api_key": current_key()}, headers={"Cache-Control": "no-store"})

    @router.post("/key/rotate")
    def reroll():
        return JSONResponse({"api_key": rotate_key()}, headers={"Cache-Control": "no-store"})

    class WorkspaceConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")
        enabled: StrictBool
        network: StrictBool = False
        image: str = Field(min_length=1, max_length=512)
        folders: list[str] = Field(default_factory=list, max_length=32)

    @router.put("/workspace")
    def workspace(body: WorkspaceConfig):
        try:
            settings = config.validate(body.model_dump())
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        config.write(settings)
        return settings

    @app.get("/api/forged/connect")
    async def connect(subject=Depends(get_current_subject)):
        return {"username": subject, "authenticated": True}

    app.include_router(router)
    original = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(application):
        async with original(application) as state:
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(rotate_key)
            # Remote access now relies on the generated key rather than a changed password.
            from utils import remote_access_settings, lan_access_settings
            old_ready = remote_access_settings._admin_password_ready
            old_lan_ready = lan_access_settings._admin_password_ready
            remote_access_settings._admin_password_ready = lambda: True
            lan_access_settings._admin_password_ready = lambda: True
            try:
                yield state
            finally:
                remote_access_settings._admin_password_ready = old_ready
                lan_access_settings._admin_password_ready = old_lan_ready

    app.router.lifespan_context = lifespan
