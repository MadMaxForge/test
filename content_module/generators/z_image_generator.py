"""
Z-Image Generator — generates character images via local ComfyUI + w1man LoRA.

Architecture: VPS -> SSH reverse tunnel -> Laptop (ComfyUI on RTX 4070)
The SSH tunnel maps localhost:8001 on VPS to localhost:8001 on the laptop.

Business rule: ALL prompts MUST start with "A w1man," and include
explicit female descriptors ("She", "young woman", etc.).

This is a pure Python module — no LLM calls.
"""

import json
import os
import random
import time
import requests
from pathlib import Path

from content_module.core.config import (
    COMFYUI_URL,
    DIMENSIONS,
    OUTPUT_DIR,
    TIMEOUTS,
    ZIMAGE_TRIGGER,
)

# ComfyUI Workflow template — only prompt (node 7), seed (node 10),
# and dimensions (node 9) change between generations.
WORKFLOW = {
    "1": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "z_image_turbo_bf16.safetensors",
            "weight_dtype": "default",
        },
    },
    "2": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["1", 0],
            "lora_name": "REDZ15_DetailDaemonZ_lora_v1.1.safetensors",
            "strength_model": 0.50,
        },
    },
    "3": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["2", 0],
            "lora_name": "Z-Breast-Slider.safetensors",
            "strength_model": 0.45,
        },
    },
    "4": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["3", 0],
            "lora_name": "w1man.safetensors",
            "strength_model": 1.00,
        },
    },
    "5": {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {
            "model": ["4", 0],
            "shift": 3,
        },
    },
    "6": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "t5-v1_1-xxl-encoder-Q5_K_M.gguf",
            "type": "sd3",
        },
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["6", 0],
            "text": "",  # <-- prompt goes here
        },
    },
    "8": {
        "class_type": "EmptySD3LatentImage",
        "inputs": {
            "batch_size": 1,
            "height": 1920,
            "width": 1088,
        },
    },
    "9": {
        "class_type": "EmptySD3LatentImage",
        "inputs": {
            "batch_size": 1,
            "height": 1920,  # <-- changes per content_type
            "width": 1088,   # <-- changes per content_type
        },
    },
    "10": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["5", 0],
            "positive": ["7", 0],
            "negative": ["11", 0],
            "latent_image": ["9", 0],
            "seed": 0,  # <-- random seed
            "steps": 8,
            "cfg": 3.5,
            "sampler_name": "euler",
            "scheduler": "beta",
            "denoise": 1.0,
        },
    },
    "11": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["6", 0],
            "text": "",  # negative prompt (empty)
        },
    },
    "12": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "ae.safetensors",
        },
    },
    "13": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["10", 0],
            "vae": ["12", 0],
        },
    },
    "14": {
        "class_type": "SaveImage",
        "inputs": {
            "images": ["13", 0],
            "filename_prefix": "ZImage",
        },
    },
}


def check_comfyui() -> bool:
    """Check if ComfyUI is reachable via the SSH tunnel."""
    try:
        resp = requests.get(f"{COMFYUI_URL}/system_stats", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            devices = data.get("devices", [])
            if devices:
                gpu = devices[0]
                vram_total = gpu.get("vram_total", 0)
                vram_free = gpu.get("vram_free", 0)
                vram_total_gb = vram_total / (1024**3)
                vram_free_gb = vram_free / (1024**3)
                print(f"[Z-Image] Connected: {gpu.get('name', 'unknown')}")
                print(f"[Z-Image] VRAM: {vram_free_gb:.1f}/{vram_total_gb:.1f} GB free")
            return True
        return False
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return False


def validate_prompt(prompt: str) -> str:
    """
    Validate and fix Z-Image prompt.
    Ensures it starts with the w1man trigger and has female descriptors.
    """
    if not prompt.startswith(ZIMAGE_TRIGGER):
        prompt = f"{ZIMAGE_TRIGGER} {prompt}"
    return prompt


def generate_image(
    prompt: str,
    content_type: str = "post",
    poll_interval: int = 5,
    max_wait: int = 900,
) -> bytes:
    """
    Submit a workflow to ComfyUI and poll until the image is ready.

    Args:
        prompt: Text prompt (must start with "A w1man,")
        content_type: 'post' (4:5), 'story' (9:16), 'reel' (9:16)
        poll_interval: Seconds between status checks
        max_wait: Maximum wait time in seconds

    Returns:
        PNG image bytes

    Raises:
        Exception on generation failure or timeout
    """
    prompt = validate_prompt(prompt)

    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["7"]["inputs"]["text"] = prompt
    workflow["10"]["inputs"]["seed"] = random.randint(1, 2**32)

    # Set dimensions based on content type
    width, height = DIMENSIONS.get(content_type, DIMENSIONS["post"])
    workflow["9"]["inputs"]["width"] = width
    workflow["9"]["inputs"]["height"] = height
    print(f"[Z-Image] Content type: {content_type} ({width}x{height})")

    # 1. Queue the workflow prompt
    print("[Z-Image] Submitting workflow...")
    resp = requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": workflow},
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"ComfyUI submit failed ({resp.status_code}): {resp.text[:300]}")

    prompt_data = resp.json()
    prompt_id = prompt_data.get("prompt_id")
    if not prompt_id:
        raise Exception(f"No prompt_id in response: {prompt_data}")

    print(f"[Z-Image] Workflow queued: {prompt_id}")

    # 2. Poll /history/{prompt_id} until the job is done
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait:
            raise Exception(f"Timeout after {max_wait}s waiting for prompt {prompt_id}")

        time.sleep(poll_interval)

        try:
            history_resp = requests.get(
                f"{COMFYUI_URL}/history/{prompt_id}",
                timeout=30,
            )
        except requests.exceptions.ConnectionError:
            print(f"[Z-Image] Connection lost, retrying... ({elapsed:.0f}s elapsed)")
            continue

        if history_resp.status_code != 200:
            print(f"[Z-Image] History check failed ({history_resp.status_code}), retrying...")
            continue

        history = history_resp.json()

        if prompt_id not in history:
            try:
                queue_resp = requests.get(f"{COMFYUI_URL}/queue", timeout=10)
                if queue_resp.status_code == 200:
                    queue_data = queue_resp.json()
                    running = queue_data.get("queue_running", [])
                    pending = queue_data.get("queue_pending", [])
                    if running:
                        print(f"[Z-Image] Processing... ({elapsed:.0f}s elapsed)")
                    elif pending:
                        print(f"[Z-Image] In queue ({len(pending)} pending)... ({elapsed:.0f}s)")
                    else:
                        print(f"[Z-Image] Waiting... ({elapsed:.0f}s elapsed)")
            except Exception:
                print(f"[Z-Image] Waiting... ({elapsed:.0f}s elapsed)")
            continue

        # Job finished — extract output image
        job_result = history[prompt_id]

        status_data = job_result.get("status", {})
        if status_data.get("status_str") == "error":
            messages = status_data.get("messages", [])
            error_msg = str(messages) if messages else "Unknown error"
            raise Exception(f"ComfyUI workflow failed: {error_msg}")

        outputs = job_result.get("outputs", {})

        # Find SaveImage node output (node 14 in our workflow)
        images_list = []
        for node_id in ("14", "13"):
            node_output = outputs.get(node_id, {})
            if "images" in node_output and node_output["images"]:
                images_list = node_output["images"]
                break

        if not images_list:
            for node_id, node_output in outputs.items():
                if "images" in node_output and node_output["images"]:
                    images_list = node_output["images"]
                    break

        if not images_list:
            raise Exception(f"No images in output: {outputs}")

        image_info = images_list[0]
        filename = image_info.get("filename")
        subfolder = image_info.get("subfolder", "")
        img_type = image_info.get("type", "output")

        if not filename:
            raise Exception(f"No filename in image info: {image_info}")

        print(f"[Z-Image] Job completed in {elapsed:.0f}s — downloading {filename}")

        # 3. Download the generated image via /view endpoint
        view_params = {"filename": filename, "type": img_type}
        if subfolder:
            view_params["subfolder"] = subfolder

        view_resp = requests.get(
            f"{COMFYUI_URL}/view",
            params=view_params,
            timeout=60,
        )

        if view_resp.status_code != 200:
            raise Exception(f"Failed to download image ({view_resp.status_code})")

        img_bytes = view_resp.content
        if len(img_bytes) < 1000:
            raise Exception(f"Image too small ({len(img_bytes)} bytes), likely an error")

        print(f"[Z-Image] Image downloaded: {len(img_bytes) / 1024:.0f} KB")
        return img_bytes


def save_image(img_bytes: bytes, job_id: str, asset_index: int) -> str:
    """Save generated image to the job's generated directory."""
    from content_module.core.config import JOBS_DIR

    gen_dir = JOBS_DIR / job_id / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    filename = f"asset_{asset_index:02d}_zimage.png"
    filepath = gen_dir / filename
    filepath.write_bytes(img_bytes)
    print(f"[Z-Image] Saved: {filepath} ({len(img_bytes) / 1024:.0f} KB)")
    return str(filepath)
