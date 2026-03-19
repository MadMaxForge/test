"""RunPod InfiniteTalk lip-sync: submit avatar + audio, get talking-head video."""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.request
from pathlib import Path

from config import RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, AVATAR_IMAGE_PATH, GENERATED_DIR

log = logging.getLogger(__name__)

# RunPod API settings
RUNPOD_BASE_URL = "https://api.runpod.ai/v2"
POLL_INTERVAL = 15  # seconds between status checks
MAX_WAIT = 900  # 15 minutes max wait


def _build_workflow(image_name: str, audio_name: str) -> dict:
    """Build the complete WAN 2.1 InfiniteTalk ComfyUI workflow.

    Includes all processing nodes (model loaders, sampler, VAE, video combine)
    required to produce a lip-sync talking-head video.
    """
    return {
        "120": {"inputs": {"model": "Wan2_1-InfiniteTalk_Single_Q8.gguf"}, "class_type": "MultiTalkModelLoader", "_meta": {"title": "Multi/InfiniteTalk Model Loader"}},
        "122": {"inputs": {"model": "wan2.1-i2v-14b-480p-Q8_0.gguf", "base_precision": "fp16_fast", "quantization": "disabled", "load_device": "offload_device", "attention_mode": "sdpa", "rms_norm_function": "default", "block_swap_args": ["134", 0], "lora": ["138", 0], "multitalk_model": ["120", 0]}, "class_type": "WanVideoModelLoader", "_meta": {"title": "WanVideo Model Loader"}},
        "125": {"inputs": {"audio": audio_name}, "class_type": "LoadAudio", "_meta": {"title": "Load Audio"}},
        "128": {"inputs": {"steps": 7, "cfg": 1.0, "shift": 11.0, "seed": int(time.time()) % 1000000, "force_offload": True, "scheduler": "dpm++_sde", "riflex_freq_index": 0, "denoise_strength": 1, "batched_cfg": False, "rope_function": "comfy", "start_step": 0, "end_step": -1, "add_noise_to_samples": True, "model": ["122", 0], "image_embeds": ["192", 0], "text_embeds": ["241", 0], "multitalk_embeds": ["194", 0]}, "class_type": "WanVideoSampler", "_meta": {"title": "WanVideo Sampler"}},
        "129": {"inputs": {"model_name": "Wan2_1_VAE_bf16.safetensors", "precision": "bf16", "use_cpu_cache": False, "verbose": False}, "class_type": "WanVideoVAELoader", "_meta": {"title": "WanVideo VAE Loader"}},
        "130": {"inputs": {"enable_vae_tiling": False, "tile_x": 272, "tile_y": 272, "tile_stride_x": 144, "tile_stride_y": 128, "normalization": "default", "vae": ["129", 0], "samples": ["128", 0]}, "class_type": "WanVideoDecode", "_meta": {"title": "WanVideo Decode"}},
        "131": {"inputs": {"frame_rate": 25, "loop_count": 0, "filename_prefix": "Wan21/InfiniteTalk_Video", "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 19, "save_metadata": True, "trim_to_audio": False, "pingpong": False, "save_output": True, "images": ["130", 0], "audio": ["125", 0]}, "class_type": "VHS_VideoCombine", "_meta": {"title": "Video Combine"}},
        "134": {"inputs": {"blocks_to_swap": 20, "offload_img_emb": False, "offload_txt_emb": False, "use_non_blocking": True, "vace_blocks_to_swap": 0, "prefetch_blocks": 1, "block_swap_debug": False}, "class_type": "WanVideoBlockSwap", "_meta": {"title": "WanVideo Block Swap"}},
        "137": {"inputs": {"model": "TencentGameMate/chinese-wav2vec2-base", "base_precision": "fp16", "load_device": "main_device"}, "class_type": "DownloadAndLoadWav2VecModel", "_meta": {"title": "(Down)load Wav2Vec Model"}},
        "138": {"inputs": {"lora": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors", "strength": 1, "low_mem_load": False, "merge_loras": False}, "class_type": "WanVideoLoraSelect", "_meta": {"title": "WanVideo Lora Select"}},
        "170": {"inputs": {"chunk_fade_shape": "linear", "chunk_length": 10, "chunk_overlap": 0.1, "audio": ["125", 0]}, "class_type": "AudioSeparation", "_meta": {"title": "AudioSeparation"}},
        "177": {"inputs": {"backend": "inductor", "fullgraph": False, "mode": "default", "dynamic": False, "dynamo_cache_size_limit": 64, "compile_transformer_blocks_only": True, "dynamo_recompile_limit": 128, "force_parameter_static_shapes": False, "allow_unmerged_lora_compile": False}, "class_type": "WanVideoTorchCompileSettings", "_meta": {"title": "WanVideo Torch Compile Settings"}},
        "192": {"inputs": {"width": ["291", 1], "height": ["291", 2], "frame_window_size": 81, "motion_frame": 9, "force_offload": False, "colormatch": "disabled", "tiled_vae": False, "mode": "infinitetalk", "output_path": "", "vae": ["129", 0], "start_image": ["291", 0], "clip_embeds": ["237", 0]}, "class_type": "WanVideoImageToVideoMultiTalk", "_meta": {"title": "WanVideo Long I2V Multi/InfiniteTalk"}},
        "194": {"inputs": {"normalize_loudness": True, "num_frames": ["306", 1], "fps": 25, "audio_scale": 1, "audio_cfg_scale": 1, "multi_audio_type": "para", "add_noise_floor": False, "smooth_transients": False, "wav2vec_model": ["137", 0], "audio_1": ["170", 3]}, "class_type": "MultiTalkWav2VecEmbeds", "_meta": {"title": "Multi/InfiniteTalk Wav2vec2 Embeds"}},
        "237": {"inputs": {"strength_1": 1, "strength_2": 1, "crop": "center", "combine_embeds": "average", "force_offload": True, "tiles": 0, "ratio": 0.5, "clip_vision": ["238", 0], "image_1": ["291", 0]}, "class_type": "WanVideoClipVisionEncode", "_meta": {"title": "WanVideo ClipVision Encode"}},
        "238": {"inputs": {"clip_name": "clip_vision_h.safetensors"}, "class_type": "CLIPVisionLoader", "_meta": {"title": "Load CLIP Vision"}},
        "241": {"inputs": {"model_name": "umt5-xxl-enc-bf16.safetensors", "precision": "bf16", "positive_prompt": "a woman is talking.", "negative_prompt": "bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards", "quantization": "disabled", "use_disk_cache": False, "device": "gpu"}, "class_type": "WanVideoTextEncodeCached", "_meta": {"title": "WanVideo TextEncode Cached"}},
        "281": {"inputs": {"width": 480, "height": 480, "upscale_method": "lanczos", "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center", "divisible_by": 16, "device": "cpu", "image": ["284", 0]}, "class_type": "ImageResizeKJv2", "_meta": {"title": "Resize Image v2"}},
        "284": {"inputs": {"image": image_name}, "class_type": "LoadImage", "_meta": {"title": "Load Image"}},
        "291": {"inputs": {"image": ["281", 0]}, "class_type": "GetImageSizeAndCount", "_meta": {"title": "Get Image Size & Count"}},
        "305": {"inputs": {"audio": ["125", 0]}, "class_type": "AudioToAudioData", "_meta": {"title": "Audio to AudioData"}},
        "306": {"inputs": {"channel": 0, "frames_per_second": 25, "start_at_frame": 0, "limit_frames": 0, "audio": ["305", 0]}, "class_type": "AudioToFFTs", "_meta": {"title": "AudioData to FFTs"}},
    }


def submit_lipsync_job(audio_path: str, avatar_path: str | None = None) -> str | None:
    """Submit a lip-sync job to RunPod.

    Args:
        audio_path: Path to TTS audio file (mp3)
        avatar_path: Path to avatar image (png). Uses config default if None.

    Returns:
        Job ID string, or None on error.
    """
    if not RUNPOD_API_KEY or not RUNPOD_ENDPOINT_ID:
        log.error("RunPod API key or endpoint not configured")
        return None

    avatar = avatar_path or AVATAR_IMAGE_PATH
    if not avatar or not Path(avatar).exists():
        log.error("Avatar image not found: %s", avatar)
        return None

    if not Path(audio_path).exists():
        log.error("Audio file not found: %s", audio_path)
        return None

    # Read and encode files
    with open(avatar, "rb") as f:
        avatar_b64 = base64.b64encode(f.read()).decode()
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    image_name = f"avatar_{int(time.time())}.png"
    audio_name = f"audio_{int(time.time())}.mp3"

    # Build complete workflow with all InfiniteTalk nodes
    workflow = _build_workflow(image_name, audio_name)

    # RunPod ComfyUI format: both image and audio go into "images" array
    payload = {
        "input": {
            "workflow": workflow,
            "images": [
                {"name": image_name, "image": avatar_b64},
                {"name": audio_name, "image": audio_b64},
            ],
        }
    }

    url = f"{RUNPOD_BASE_URL}/{RUNPOD_ENDPOINT_ID}/run"
    data = json.dumps(payload).encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {RUNPOD_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        job_id = result.get("id")
        log.info("RunPod job submitted: %s (workflow nodes: %d)", job_id, len(workflow))
        return job_id
    except Exception as e:
        log.error("RunPod job submission failed: %s", e)
        return None


def poll_job(job_id: str) -> dict | None:
    """Poll RunPod job until completion.

    Returns:
        Result dict with 'status' and 'output', or None on error.
    """
    url = f"{RUNPOD_BASE_URL}/{RUNPOD_ENDPOINT_ID}/status/{job_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
    )

    start = time.time()
    while time.time() - start < MAX_WAIT:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            log.warning("Poll error (will retry): %s", e)
            time.sleep(POLL_INTERVAL)
            continue

        status = result.get("status", "UNKNOWN")
        elapsed = int(time.time() - start)
        log.info("[%ds] RunPod job %s: %s", elapsed, job_id, status)

        if status == "COMPLETED":
            return result
        elif status == "FAILED":
            error_info = result.get("error", "unknown")
            log.error("RunPod job failed: %s. Raw response: %s",
                       error_info, json.dumps(result.get("output", result.get("error", ""))))
            return None
        elif status in ("IN_QUEUE", "IN_PROGRESS"):
            time.sleep(POLL_INTERVAL)
        else:
            log.warning("Unknown RunPod status: %s", status)
            time.sleep(POLL_INTERVAL)

    log.error("RunPod job timed out after %ds", MAX_WAIT)
    return None


def save_lipsync_video(result: dict, output_filename: str | None = None) -> str | None:
    """Extract and save lip-sync video from RunPod result.

    Returns:
        Path to saved video file, or None on error.
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if output_filename is None:
        output_filename = f"lipsync_{int(time.time())}.mp4"
    output_path = GENERATED_DIR / output_filename

    output = result.get("output", {})
    images = output.get("images", [])

    for item in images:
        data = item.get("data", "")
        dtype = item.get("type", "")
        if dtype == "base64" and data:
            video_bytes = base64.b64decode(data)
            output_path.write_bytes(video_bytes)
            size_mb = len(video_bytes) / 1024 / 1024
            log.info("Lip-sync video saved: %s (%.1fMB)", output_path, size_mb)
            return str(output_path)

    log.error("No video data found in RunPod result. Output keys: %s", list(output.keys()))
    return None


def generate_lipsync_video(audio_path: str, avatar_path: str | None = None) -> str | None:
    """Full pipeline: submit job, wait for completion, save video.

    Args:
        audio_path: Path to TTS audio file
        avatar_path: Path to avatar image (uses config default if None)

    Returns:
        Path to lip-sync video file, or None on error.
    """
    log.info("Starting lip-sync generation...")

    # Submit job
    job_id = submit_lipsync_job(audio_path, avatar_path)
    if not job_id:
        return None

    # Poll until done
    result = poll_job(job_id)
    if not result:
        return None

    # Save video
    return save_lipsync_video(result)
