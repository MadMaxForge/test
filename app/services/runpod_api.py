import httpx
import os
from typing import Optional


RUNPOD_BASE_URL = "https://api.runpod.ai/v2"


def _get_api_key() -> str:
    key = os.getenv("RUNPOD_API_KEY", "")
    if not key:
        raise ValueError("RUNPOD_API_KEY not set")
    return key


def _get_endpoint_id() -> str:
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "")
    if not endpoint_id:
        raise ValueError("RUNPOD_ENDPOINT_ID not set. Create a ComfyUI serverless endpoint on RunPod first.")
    return endpoint_id


async def submit_comfyui_job(
    workflow: dict,
    files: list[dict] | None = None,
) -> dict:
    """Submit a ComfyUI workflow to RunPod serverless endpoint.
    
    Uses ashleykleynhans/runpod-worker-comfyui format:
    {"input": {"workflow": "custom", "payload": {workflow_json}}}
    
    Files are embedded as __files__ inside the payload dict so they
    survive the worker's input validation (which only allows 'workflow'
    and 'payload' keys). A patched handler on the worker extracts
    __files__ from the payload and saves them to ComfyUI's input
    directory before processing the workflow.
    
    Args:
        workflow: ComfyUI workflow JSON (API format)
        files: Optional list of {"name": str, "data": str (base64)} dicts
    
    Returns:
        dict with 'id' (job id) and 'status'
    """
    endpoint_id = _get_endpoint_id()

    # Embed files inside workflow payload so they pass the worker's
    # input validator (only 'workflow' and 'payload' are accepted).
    if files:
        workflow["__files__"] = files

    input_data: dict = {
        "workflow": "custom",
        "payload": workflow,
    }

    payload: dict = {"input": input_data}

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{RUNPOD_BASE_URL}/{endpoint_id}/run",
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def check_job_status(runpod_job_id: str) -> dict:
    """Check the status of a RunPod job.
    
    Returns:
        dict with 'status' and optionally 'output'
    """
    endpoint_id = _get_endpoint_id()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{RUNPOD_BASE_URL}/{endpoint_id}/status/{runpod_job_id}",
            headers={"Authorization": f"Bearer {_get_api_key()}"},
        )
        resp.raise_for_status()
        return resp.json()


async def cancel_job(runpod_job_id: str) -> dict:
    """Cancel a RunPod job."""
    endpoint_id = _get_endpoint_id()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{RUNPOD_BASE_URL}/{endpoint_id}/cancel/{runpod_job_id}",
            headers={"Authorization": f"Bearer {_get_api_key()}"},
        )
        resp.raise_for_status()
        return resp.json()
