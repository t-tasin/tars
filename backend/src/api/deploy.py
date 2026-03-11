"""Self-deploy API endpoint for T.A.R.S. (HC-02: requires confirmation)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from src.api.schemas import DeployRequest, ErrorDetail, ErrorResponse
from src.api.websocket import get_ws_manager
from src.dependencies import verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["deploy"])

# Path to the deploy script inside the container
_DEPLOY_SCRIPT = Path("/opt/tars/deploy/scripts/deploy.sh")
_NODE1_DIR = Path("/opt/tars/deploy/node1")
_NODE2_DIR = Path("/opt/tars/deploy/node2")


@router.post("/deploy")
async def trigger_deploy(
    body: DeployRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
) -> dict[str, Any]:
    """Trigger a self-deploy: docker compose pull && up -d.

    Requires ``confirm: true`` in the request body (HC-02).
    Sends progress updates via WebSocket ``deploy_status`` messages.
    If ``notify_only`` is set, just reports current deploy state without acting.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="confirmation_required",
                    message="Set confirm=true to proceed with deployment",
                ),
            ).model_dump(),
        )

    if body.notify_only:
        log.info("deploy_notify_only")
        return {"status": "notify_only", "message": "No deploy triggered"}

    ws = get_ws_manager()

    # Broadcast: deploy starting
    await ws.broadcast("deploy_status", {
        "phase": "starting",
        "message": "Deploy initiated",
        "sha": body.sha,
    })

    log.info("deploy_triggered", sha=body.sha)

    # Run deploy in background so we can stream progress
    asyncio.create_task(_run_deploy(body.sha, ws))

    return {
        "status": "deploying",
        "message": "Deploy started. Progress updates via WebSocket.",
        "sha": body.sha,
    }


async def _run_deploy(sha: str | None, ws: Any) -> None:
    """Run deploy.sh for each node and stream progress via WebSocket."""
    try:
        # Deploy Node 1
        await ws.broadcast("deploy_status", {
            "phase": "pulling",
            "node": "node1",
            "message": "Pulling latest images for Node 1...",
        })

        node1_result = await _run_deploy_script(_NODE1_DIR)

        await ws.broadcast("deploy_status", {
            "phase": "pulled",
            "node": "node1",
            "message": "Node 1 updated",
            "output": node1_result,
        })

        # Deploy Node 2 (if reachable)
        await ws.broadcast("deploy_status", {
            "phase": "pulling",
            "node": "node2",
            "message": "Pulling latest images for Node 2...",
        })

        node2_result = await _run_deploy_script(_NODE2_DIR)

        await ws.broadcast("deploy_status", {
            "phase": "pulled",
            "node": "node2",
            "message": "Node 2 updated",
            "output": node2_result,
        })

        # Done
        await ws.broadcast("deploy_status", {
            "phase": "complete",
            "message": "Deploy complete",
            "sha": sha,
        })

        log.info("deploy_complete", sha=sha)

    except Exception as exc:
        log.error("deploy_failed", error=str(exc), sha=sha)

        await ws.broadcast("deploy_status", {
            "phase": "failed",
            "message": f"Deploy failed: {exc}",
            "sha": sha,
        })


async def _run_deploy_script(node_dir: Path) -> str:
    """Run deploy.sh for a given node directory.

    Uses create_subprocess_exec (not shell) for safety.
    Returns the combined stdout/stderr output.
    """
    script = str(_DEPLOY_SCRIPT)
    work_dir = str(node_dir)

    proc = await asyncio.create_subprocess_exec(
        "bash", script, work_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    output = stdout.decode() if stdout else ""

    if proc.returncode != 0:
        raise RuntimeError(
            f"deploy.sh failed for {node_dir} (exit {proc.returncode}): {output}"
        )

    return output
