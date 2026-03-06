import copy
import json
import os
import uuid
import base64
from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import JobStatus, JobResponse
from app.services import elevenlabs, s3_storage, runpod_api

# Path prefix on the Network Volume where ComfyUI's input directory lives.
# The ashleykleynhans worker mounts /runpod-volume and runs ComfyUI from there.
# S3 key = this prefix + "/input/" + filename
COMFYUI_INPUT_PREFIX = os.getenv("COMFYUI_INPUT_PREFIX", "runpod-slim/ComfyUI/input")


# In-memory job store (for MVP; can migrate to SQLite later)
_jobs: dict[str, dict] = {}


def _create_job(text: str, voice_id: Optional[str] = None) -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": JobStatus.QUEUED,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "voice_id": voice_id,
        "audio_url": None,
        "video_url": None,
        "error": None,
        "runpod_job_id": None,
    }
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


def update_job(job_id: str, **kwargs: object) -> None:
    if job_id in _jobs:
        _jobs[job_id].update(kwargs)


async def generate_video(
    job_id: str,
    text: str,
    image_bytes: bytes,
    image_filename: str,
    voice_id: Optional[str] = None,
    model_id: str = "eleven_multilingual_v2",
    image_content_type: str = "image/jpeg",
) -> None:
    """Full pipeline: text -> TTS -> upload -> RunPod ComfyUI -> video.
    
    This runs as a background task.
    """
    try:
        # Step 1: Generate audio via ElevenLabs
        update_job(job_id, status=JobStatus.GENERATING_AUDIO)
        audio_bytes = await elevenlabs.text_to_speech(text, voice_id, model_id)

        # Step 2: Upload audio and image to S3 (best-effort backup)
        update_job(job_id, status=JobStatus.UPLOADING)
        audio_url = None
        try:
            audio_key = f"jobs/{job_id}/audio.mp3"
            image_key = f"jobs/{job_id}/{image_filename}"
            audio_url = s3_storage.upload_file(audio_bytes, audio_key, "audio/mpeg")
            s3_storage.upload_file(image_bytes, image_key, image_content_type)
        except Exception:
            pass  # S3 upload is best-effort; pipeline continues without it

        update_job(job_id, audio_url=audio_url)

        # Step 3: Submit ComfyUI workflow to RunPod
        try:
            update_job(job_id, status=JobStatus.GENERATING_VIDEO)

            # Build the WAN 2.1 InfiniteTalk workflow with dynamic filenames
            audio_input_name = f"audio_{job_id}.mp3"
            image_input_name = f"image_{job_id}.png"

            # Send audio and image as base64 in the RunPod payload.
            # The worker's patched handler saves them to ComfyUI's input
            # directory before processing the workflow.
            files = [
                {
                    "name": audio_input_name,
                    "data": base64.b64encode(audio_bytes).decode(),
                },
                {
                    "name": image_input_name,
                    "data": base64.b64encode(image_bytes).decode(),
                },
            ]

            workflow = _build_lipsync_workflow(audio_input_name, image_input_name)

            result = await runpod_api.submit_comfyui_job(
                workflow=workflow,
                files=files,
            )

            runpod_job_id = result.get("id")
            update_job(job_id, runpod_job_id=runpod_job_id)

        except ValueError as e:
            # RunPod endpoint not configured — audio was generated, skip video
            update_job(
                job_id,
                status=JobStatus.COMPLETED,
                error=f"Video generation skipped: {e}. Audio was generated successfully.",
            )

    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))


async def poll_runpod_status(job_id: str) -> Optional[str]:
    """Poll RunPod for job status. Returns video URL if completed."""
    job = get_job(job_id)
    if not job or not job.get("runpod_job_id"):
        return None

    try:
        result = await runpod_api.check_job_status(job["runpod_job_id"])
        status = result.get("status", "")

        if status == "COMPLETED":
            output = result.get("output", {})
            video_url = _extract_video_url(output, job_id=job_id)
            update_job(job_id, status=JobStatus.COMPLETED, video_url=video_url)
            return video_url

        elif status == "FAILED":
            error = result.get("error", "RunPod job failed")
            update_job(job_id, status=JobStatus.FAILED, error=str(error))

        # Still in progress
        return None

    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=f"Polling error: {e}")
        return None


def create_job_entry(text: str, voice_id: Optional[str] = None) -> str:
    """Public interface to create a new job entry."""
    return _create_job(text, voice_id)


def _extract_video_url(output: object, job_id: str = "") -> Optional[str]:
    """Extract video URL from RunPod worker output.
    
    ashleykleynhans/runpod-worker-comfyui returns:
    {"images": ["<base64_data>", ...]}
    
    Each entry is a base64-encoded file from ComfyUI's output directory.
    We decode the base64 data, upload to S3, and return a presigned URL.
    
    Also handles official worker-comfyui v5+ format:
    {"images": [{"filename": "...", "type": "s3_url", "data": "..."}]}
    """
    if isinstance(output, str):
        return output
    if not isinstance(output, dict):
        return None

    images_list = output.get("images", [])
    if isinstance(images_list, list) and len(images_list) > 0:
        first_item = images_list[0]

        # ashleykleynhans format: list of base64 strings
        if isinstance(first_item, str):
            try:
                video_bytes = base64.b64decode(first_item)
                video_key = f"jobs/{job_id}/output_video.mp4" if job_id else f"jobs/unknown/output_video.mp4"
                s3_storage.upload_file(video_bytes, video_key, "video/mp4")
                return s3_storage.generate_presigned_url(video_key, expiration=86400)
            except Exception:
                # If decode/upload fails, return raw data reference
                return None

        # Official worker-comfyui v5+ format: list of dicts
        if isinstance(first_item, dict):
            for item in images_list:
                if not isinstance(item, dict):
                    continue
                data = item.get("data", "")
                item_type = item.get("type", "")
                if item_type == "s3_url" and data:
                    return str(data)
            # Fallback: any dict with data
            for item in images_list:
                if isinstance(item, dict) and item.get("data"):
                    return str(item["data"])

    # Legacy format
    return output.get("video_url") or output.get("message")


# ---------- WAN 2.1 InfiniteTalk Workflow Template ----------
# Exported from user's proven ComfyUI workflow (API format).
# Node 125 (LoadAudio) and Node 284 (LoadImage) have dynamic filenames.
_WAN21_WORKFLOW_TEMPLATE: dict = {
    "120": {
        "inputs": {"model": "Wan2_1-InfiniteTalk_Single_Q8.gguf"},
        "class_type": "MultiTalkModelLoader",
        "_meta": {"title": "Multi/InfiniteTalk Model Loader"},
    },
    "122": {
        "inputs": {
            "model": "wan2.1-i2v-14b-480p-Q8_0.gguf",
            "base_precision": "fp16_fast",
            "quantization": "disabled",
            "load_device": "offload_device",
            "attention_mode": "sdpa",
            "rms_norm_function": "default",
            "block_swap_args": ["134", 0],
            "lora": ["138", 0],
            "multitalk_model": ["120", 0],
        },
        "class_type": "WanVideoModelLoader",
        "_meta": {"title": "WanVideo Model Loader"},
    },
    "125": {
        "inputs": {
            "audio": "audio-input.mp3",
            "audioUI": "",
            "choose file to upload": "Audio",
        },
        "class_type": "LoadAudio",
        "_meta": {"title": "Load Audio"},
    },
    "128": {
        "inputs": {
            "steps": 7,
            "cfg": 1.0000000000000002,
            "shift": 11.000000000000002,
            "seed": 2,
            "force_offload": True,
            "scheduler": "dpm++_sde",
            "riflex_freq_index": 0,
            "denoise_strength": 1,
            "batched_cfg": False,
            "rope_function": "comfy",
            "start_step": 0,
            "end_step": -1,
            "add_noise_to_samples": True,
            "model": ["122", 0],
            "image_embeds": ["192", 0],
            "text_embeds": ["241", 0],
            "multitalk_embeds": ["194", 0],
        },
        "class_type": "WanVideoSampler",
        "_meta": {"title": "WanVideo Sampler"},
    },
    "129": {
        "inputs": {
            "model_name": "Wan2_1_VAE_bf16.safetensors",
            "precision": "bf16",
            "use_cpu_cache": False,
            "verbose": False,
        },
        "class_type": "WanVideoVAELoader",
        "_meta": {"title": "WanVideo VAE Loader"},
    },
    "130": {
        "inputs": {
            "enable_vae_tiling": False,
            "tile_x": 272,
            "tile_y": 272,
            "tile_stride_x": 144,
            "tile_stride_y": 128,
            "normalization": "default",
            "vae": ["129", 0],
            "samples": ["128", 0],
        },
        "class_type": "WanVideoDecode",
        "_meta": {"title": "WanVideo Decode"},
    },
    "131": {
        "inputs": {
            "frame_rate": 25,
            "loop_count": 0,
            "filename_prefix": "Wan21/InfiniteTalk_Video",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
            "images": ["130", 0],
            "audio": ["125", 0],
        },
        "class_type": "VHS_VideoCombine",
        "_meta": {"title": "Video Combine"},
    },
    "134": {
        "inputs": {
            "blocks_to_swap": 20,
            "offload_img_emb": False,
            "offload_txt_emb": False,
            "use_non_blocking": True,
            "vace_blocks_to_swap": 0,
            "prefetch_blocks": 1,
            "block_swap_debug": False,
        },
        "class_type": "WanVideoBlockSwap",
        "_meta": {"title": "WanVideo Block Swap"},
    },
    "137": {
        "inputs": {
            "model": "TencentGameMate/chinese-wav2vec2-base",
            "base_precision": "fp16",
            "load_device": "main_device",
        },
        "class_type": "DownloadAndLoadWav2VecModel",
        "_meta": {"title": "(Down)load Wav2Vec Model"},
    },
    "138": {
        "inputs": {
            "lora": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors",
            "strength": 1,
            "low_mem_load": False,
            "merge_loras": False,
        },
        "class_type": "WanVideoLoraSelect",
        "_meta": {"title": "WanVideo Lora Select"},
    },
    "170": {
        "inputs": {
            "chunk_fade_shape": "linear",
            "chunk_length": 10,
            "chunk_overlap": 0.1,
            "audio": ["125", 0],
        },
        "class_type": "AudioSeparation",
        "_meta": {"title": "AudioSeparation"},
    },
    "177": {
        "inputs": {
            "backend": "inductor",
            "fullgraph": False,
            "mode": "default",
            "dynamic": False,
            "dynamo_cache_size_limit": 64,
            "compile_transformer_blocks_only": True,
            "dynamo_recompile_limit": 128,
            "force_parameter_static_shapes": False,
            "allow_unmerged_lora_compile": False,
        },
        "class_type": "WanVideoTorchCompileSettings",
        "_meta": {"title": "WanVideo Torch Compile Settings"},
    },
    "192": {
        "inputs": {
            "width": ["291", 1],
            "height": ["291", 2],
            "frame_window_size": 81,
            "motion_frame": 9,
            "force_offload": False,
            "colormatch": "disabled",
            "tiled_vae": False,
            "mode": "infinitetalk",
            "output_path": "",
            "vae": ["129", 0],
            "start_image": ["291", 0],
            "clip_embeds": ["237", 0],
        },
        "class_type": "WanVideoImageToVideoMultiTalk",
        "_meta": {"title": "WanVideo Long I2V Multi/InfiniteTalk"},
    },
    "194": {
        "inputs": {
            "normalize_loudness": True,
            "num_frames": ["306", 1],
            "fps": 25,
            "audio_scale": 1,
            "audio_cfg_scale": 1,
            "multi_audio_type": "para",
            "add_noise_floor": False,
            "smooth_transients": False,
            "wav2vec_model": ["137", 0],
            "audio_1": ["170", 3],
        },
        "class_type": "MultiTalkWav2VecEmbeds",
        "_meta": {"title": "Multi/InfiniteTalk Wav2vec2 Embeds"},
    },
    "237": {
        "inputs": {
            "strength_1": 1,
            "strength_2": 1,
            "crop": "center",
            "combine_embeds": "average",
            "force_offload": True,
            "tiles": 0,
            "ratio": 0.5,
            "clip_vision": ["238", 0],
            "image_1": ["291", 0],
        },
        "class_type": "WanVideoClipVisionEncode",
        "_meta": {"title": "WanVideo ClipVision Encode"},
    },
    "238": {
        "inputs": {"clip_name": "clip_vision_h.safetensors"},
        "class_type": "CLIPVisionLoader",
        "_meta": {"title": "Load CLIP Vision"},
    },
    "241": {
        "inputs": {
            "model_name": "umt5-xxl-enc-bf16.safetensors",
            "precision": "bf16",
            "positive_prompt": "a woman is talking.",
            "negative_prompt": "bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards",
            "quantization": "disabled",
            "use_disk_cache": False,
            "device": "gpu",
        },
        "class_type": "WanVideoTextEncodeCached",
        "_meta": {"title": "WanVideo TextEncode Cached"},
    },
    "245": {
        "inputs": {"value": 640},
        "class_type": "INTConstant",
        "_meta": {"title": "Width"},
    },
    "246": {
        "inputs": {"value": 640},
        "class_type": "INTConstant",
        "_meta": {"title": "Height"},
    },
    "270": {
        "inputs": {"value": 1000},
        "class_type": "INTConstant",
        "_meta": {"title": "Max frames"},
    },
    "281": {
        "inputs": {
            "width": 832,
            "height": 480,
            "upscale_method": "lanczos",
            "keep_proportion": "crop",
            "pad_color": "0, 0, 0",
            "crop_position": "center",
            "divisible_by": 16,
            "device": "cpu",
            "image": ["284", 0],
        },
        "class_type": "ImageResizeKJv2",
        "_meta": {"title": "Resize Image v2"},
    },
    "284": {
        "inputs": {"image": "image-input.png"},
        "class_type": "LoadImage",
        "_meta": {"title": "Load Image"},
    },
    "291": {
        "inputs": {"image": ["281", 0]},
        "class_type": "GetImageSizeAndCount",
        "_meta": {"title": "Get Image Size & Count"},
    },
    "293": {
        "inputs": {
            "preview": "357",
            "previewMode": None,
            "source": ["194", 2],
        },
        "class_type": "PreviewAny",
        "_meta": {"title": "Preview Any"},
    },
    "305": {
        "inputs": {"audio": ["125", 0]},
        "class_type": "AudioToAudioData",
        "_meta": {"title": "Audio to AudioData"},
    },
    "306": {
        "inputs": {
            "channel": 0,
            "frames_per_second": 25,
            "start_at_frame": 0,
            "limit_frames": 0,
            "audio": ["305", 0],
        },
        "class_type": "AudioToFFTs",
        "_meta": {"title": "AudioData to FFTs"},
    },
}


def _build_lipsync_workflow(audio_filename: str, image_filename: str) -> dict:
    """Build a WAN 2.1 InfiniteTalk ComfyUI workflow with dynamic input filenames.
    
    Args:
        audio_filename: Filename for the audio file in ComfyUI input dir
        image_filename: Filename for the image file in ComfyUI input dir
    
    Returns:
        Complete ComfyUI API-format workflow dict.
    """
    workflow = copy.deepcopy(_WAN21_WORKFLOW_TEMPLATE)
    # Set dynamic filenames for input nodes
    workflow["125"]["inputs"]["audio"] = audio_filename
    workflow["284"]["inputs"]["image"] = image_filename
    return workflow
