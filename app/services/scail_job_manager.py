import copy
import uuid
import base64
from datetime import datetime, timezone
from typing import Optional

from app.models.scail_schemas import ScailJobStatus
from app.services import scail_runpod_api


# In-memory job store for SCAIL motion control jobs
_scail_jobs: dict[str, dict] = {}

# Resolution options: index -> pixels
RESOLUTION_OPTIONS = {
    1: 320,   # Fast test
    2: 640,   # Low res
    3: 832,   # Medium res (default)
    4: 960,   # High res
    5: 1280,  # Max res
}


def _create_scail_job(
    prompt: str,
    negative_prompt: Optional[str] = None,
    resolution: int = 832,
) -> str:
    job_id = str(uuid.uuid4())
    _scail_jobs[job_id] = {
        "job_id": job_id,
        "status": ScailJobStatus.QUEUED,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "resolution": resolution,
        "video_url": None,
        "error": None,
        "runpod_job_id": None,
    }
    return job_id


def get_scail_job(job_id: str) -> Optional[dict]:
    return _scail_jobs.get(job_id)


def list_scail_jobs() -> list[dict]:
    return sorted(_scail_jobs.values(), key=lambda j: j["created_at"], reverse=True)


def update_scail_job(job_id: str, **kwargs: object) -> None:
    if job_id in _scail_jobs:
        _scail_jobs[job_id].update(kwargs)


def create_scail_job_entry(
    prompt: str,
    negative_prompt: Optional[str] = None,
    resolution: int = 832,
) -> str:
    """Public interface to create a new SCAIL job entry."""
    return _create_scail_job(prompt, negative_prompt, resolution)


async def generate_scail_video(
    job_id: str,
    prompt: str,
    video_bytes: bytes,
    video_filename: str,
    image_bytes: bytes,
    image_filename: str,
    negative_prompt: Optional[str] = None,
    resolution: int = 832,
) -> None:
    """Full pipeline: video + image + text -> SCAIL motion control video.

    This runs as a background task.
    """
    try:
        update_scail_job(job_id, status=ScailJobStatus.UPLOADING)

        # Encode video and image as base64 for ComfyUI input directory
        video_b64 = base64.b64encode(video_bytes).decode("utf-8")
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Build the SCAIL workflow with dynamic filenames and parameters
        video_input_name = f"video_{job_id}.mp4"
        image_input_name = f"image_{job_id}.png"

        workflow = _build_scail_workflow(
            video_filename=video_input_name,
            image_filename=image_input_name,
            prompt=prompt,
            negative_prompt=negative_prompt,
            resolution=resolution,
        )

        # Upload files to ComfyUI input directory via worker's images array
        input_files = [
            {"name": video_input_name, "image": video_b64},
            {"name": image_input_name, "image": image_b64},
        ]

        update_scail_job(job_id, status=ScailJobStatus.GENERATING_VIDEO)

        result = await scail_runpod_api.submit_scail_job(
            workflow=workflow,
            files=input_files,
        )

        runpod_job_id = result.get("id")
        update_scail_job(job_id, runpod_job_id=runpod_job_id)

    except Exception as e:
        update_scail_job(job_id, status=ScailJobStatus.FAILED, error=str(e))


async def poll_scail_runpod_status(job_id: str) -> Optional[str]:
    """Poll RunPod for SCAIL job status. Returns video URL if completed."""
    job = get_scail_job(job_id)
    if not job or not job.get("runpod_job_id"):
        return None

    try:
        result = await scail_runpod_api.check_scail_job_status(job["runpod_job_id"])
        status = result.get("status", "")

        if status == "COMPLETED":
            output = result.get("output", {})
            video_url = _extract_video_url(output)
            update_scail_job(job_id, status=ScailJobStatus.COMPLETED, video_url=video_url)
            return video_url

        elif status == "FAILED":
            error = result.get("error", "RunPod job failed")
            update_scail_job(job_id, status=ScailJobStatus.FAILED, error=str(error))

        return None

    except Exception as e:
        update_scail_job(job_id, status=ScailJobStatus.FAILED, error=f"Polling error: {e}")
        return None


def _extract_video_url(output: object) -> Optional[str]:
    """Extract video/image URL from RunPod worker-comfyui output."""
    if isinstance(output, str):
        return output
    if not isinstance(output, dict):
        return None

    images_list = output.get("images", [])
    if isinstance(images_list, list):
        for item in images_list:
            if not isinstance(item, dict):
                continue
            data = item.get("data", "")
            item_type = item.get("type", "")
            filename = item.get("filename", "")
            if filename.endswith((".mp4", ".webm", ".avi", ".mov")):
                if item_type == "s3_url":
                    return str(data)
            if item_type == "s3_url" and data:
                return str(data)
        for item in images_list:
            if isinstance(item, dict) and item.get("data"):
                return str(item["data"])

    return output.get("video_url") or output.get("message")


# ---------- WAN SCAIL + Flux Klein Workflow Template ----------
# Motion control workflow: reference video + character image + text prompt -> video
# Uses WAN SCAIL for 3D pose tracking and Flux Klein for first frame generation.
#
# Dynamic inputs:
#   - Node "458" (VHS_LoadVideo): video filename
#   - Node "484" (LoadImage): image filename
#   - Node "487" (CLIPTextEncode): positive prompt text (Flux Klein)
#   - Node "16" (WanVideoTextEncode): positive prompt for WAN (motion description)
#   - Resolution via ImpactSwitch selector

_DEFAULT_NEGATIVE_PROMPT = (
    "bright tones, overexposed, static, blurred details, subtitles, style, works, "
    "paintings, images, static, overall gray, worst quality, low quality, "
    "JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, "
    "poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, "
    "still picture, messy background, three legs, many people in the background, "
    "walking backwards"
)

_SCAIL_WORKFLOW_TEMPLATE: dict = {
    # ===== VIDEO & IMAGE INPUTS =====
    "458": {
        "inputs": {
            "video": "reference_video.mp4",
            "force_rate": 16,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": 0,
            "skip_first_frames": 0,
            "select_every_nth": 1,
        },
        "class_type": "VHS_LoadVideo",
        "_meta": {"title": "Load Reference Video"},
    },
    "484": {
        "inputs": {"image": "reference_image.png"},
        "class_type": "LoadImage",
        "_meta": {"title": "Load Character Reference Image"},
    },
    # ===== RESOLUTION SELECTION =====
    # ImpactSwitch selects resolution based on index (1-5)
    # We set the resolution directly on the resize nodes instead
    # ===== VIDEO PROCESSING =====
    "462": {
        "inputs": {
            "image": ["458", 0],
            "width": 832,
            "height": 832,
            "upscale_method": "nearest-exact",
            "keep_proportion": "resize",
            "pad_color": "0, 0, 0",
            "crop_position": "center",
            "divisible_by": 64,
            "device": "cpu",
        },
        "class_type": "ImageResizeKJv2",
        "_meta": {"title": "Resize Video Frames"},
    },
    "378": {
        "inputs": {"video_info": ["458", 3]},
        "class_type": "VHS_VideoInfo",
        "_meta": {"title": "Video Info"},
    },
    "490": {
        "inputs": {"frames": ["458", 0], "index": 0},
        "class_type": "Frame Select",
        "_meta": {"title": "Frame Select"},
    },
    # ===== CHARACTER IMAGE RESIZE =====
    "107": {
        "inputs": {
            "image": ["484", 0],
            "width": 832,
            "height": 832,
            "upscale_method": "nearest-exact",
            "keep_proportion": "resize",
            "pad_color": "0, 0, 0",
            "crop_position": "center",
            "divisible_by": 64,
            "device": "cpu",
        },
        "class_type": "ImageResizeKJv2",
        "_meta": {"title": "Resize Character Image"},
    },
    # ===== HALF RESOLUTION FOR WAN GENERATION =====
    "497": {
        "inputs": {"a": ["462", 1], "b": 0, "combine": 2, "action": "divide"},
        "class_type": "easy mathInt",
        "_meta": {"title": "Half Width"},
    },
    "498": {
        "inputs": {"a": ["462", 2], "b": 0, "combine": 2, "action": "divide"},
        "class_type": "easy mathInt",
        "_meta": {"title": "Half Height"},
    },
    # ===== FLUX KLEIN SECTION (First Frame Generation) =====
    "488": {
        "inputs": {
            "unet_name": "flux-2-klein-9b-fp8.safetensors",
            "weight_dtype": "default",
        },
        "class_type": "UNETLoader",
        "_meta": {"title": "Load Flux Klein"},
    },
    "481": {
        "inputs": {"model": ["488", 0], "backend": "auto", "force": True},
        "class_type": "PathchSageAttentionKJ",
        "_meta": {"title": "Sage Attention"},
    },
    "486": {
        "inputs": {
            "clip_name": "qwen_3_8b_fp8mixed.safetensors",
            "type": "flux2",
            "device": "default",
        },
        "class_type": "CLIPLoader",
        "_meta": {"title": "Load CLIP Text Encoder"},
    },
    "483": {
        "inputs": {"vae_name": "flux2-vae.safetensors"},
        "class_type": "VAELoader",
        "_meta": {"title": "Load Flux2 VAE"},
    },
    "487": {
        "inputs": {
            "clip": ["486", 0],
            "text": "a person dancing",
        },
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "Positive Prompt (Flux Klein)"},
    },
    "485": {
        "inputs": {"clip": ["486", 0], "text": ""},
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "Negative Prompt (Flux Klein)"},
    },
    # DW Pose on first frame of reference video
    "509": {
        "inputs": {
            "image": ["490", 0],
            "detect_hand": "enable",
            "detect_body": "enable",
            "detect_face": "disable",
            "resolution": 512,
            "bbox_detector": "yolox_l.torchscript.pt",
            "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
            "scale_stick_for_animal_face": "disable",
        },
        "class_type": "DWPreprocessor",
        "_meta": {"title": "DW Preprocessor"},
    },
    # Scale and encode reference image for Flux Klein conditioning
    "482": {
        "inputs": {
            "image": ["484", 0],
            "upscale_method": "nearest-exact",
            "megapixels": 1.2,
            "rounding": 1,
        },
        "class_type": "ImageScaleToTotalPixels",
        "_meta": {"title": "Scale Reference Image"},
    },
    "476": {
        "inputs": {"pixels": ["482", 0], "vae": ["483", 0]},
        "class_type": "VAEEncode",
        "_meta": {"title": "VAE Encode Reference"},
    },
    "506": {
        "inputs": {"pixels": ["509", 0], "vae": ["483", 0]},
        "class_type": "VAEEncode",
        "_meta": {"title": "VAE Encode DW Pose"},
    },
    # ReferenceLatent chain for Flux Klein conditioning
    "479": {
        "inputs": {"conditioning": ["487", 0], "latent": ["476", 0]},
        "class_type": "ReferenceLatent",
        "_meta": {"title": "Reference Latent Positive"},
    },
    "474": {
        "inputs": {"conditioning": ["479", 0], "latent": ["476", 0]},
        "class_type": "ReferenceLatent",
        "_meta": {"title": "Reference Latent Chain"},
    },
    "505": {
        "inputs": {"conditioning": ["474", 0], "latent": ["506", 0]},
        "class_type": "ReferenceLatent",
        "_meta": {"title": "Reference Latent DW Pose"},
    },
    # Empty latent for Flux Klein generation
    "480": {
        "inputs": {
            "width": ["378", 3],
            "height": ["378", 4],
            "batch_size": 1,
        },
        "class_type": "EmptyFlux2LatentImage",
        "_meta": {"title": "Empty Flux2 Latent"},
    },
    # Flux Klein KSampler
    "507": {
        "inputs": {
            "model": ["481", 0],
            "positive": ["505", 0],
            "negative": ["485", 0],
            "latent_image": ["480", 0],
            "seed": 331849429007016,
            "control_after_generate": "fixed",
            "steps": 8,
            "cfg": 1,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1,
        },
        "class_type": "KSampler",
        "_meta": {"title": "Flux Klein Sampler"},
    },
    # Decode Flux Klein output (generated first frame)
    "494": {
        "inputs": {"samples": ["507", 0], "vae": ["483", 0]},
        "class_type": "VAEDecode",
        "_meta": {"title": "Decode First Frame"},
    },
    # ===== WAN SCAIL SECTION (Video Generation) =====
    "39": {
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
        "_meta": {"title": "Block Swap"},
    },
    "56": {
        "inputs": {
            "lora": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors",
            "strength": 1,
            "low_mem_load": False,
            "merge_loras": False,
        },
        "class_type": "WanVideoLoraSelect",
        "_meta": {"title": "Speed LoRA"},
    },
    "22": {
        "inputs": {
            "model": "Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors",
            "base_precision": "bf16",
            "quantization": "disabled",
            "load_device": "offload_device",
            "attention_mode": "sdpa",
            "rms_norm_function": "default",
            "block_swap_args": ["39", 0],
            "lora": ["56", 0],
        },
        "class_type": "WanVideoModelLoader",
        "_meta": {"title": "WAN SCAIL Model Loader"},
    },
    "92": {
        "inputs": {"model": ["22", 0], "block_swap_args": ["39", 0]},
        "class_type": "WanVideoSetBlockSwap",
        "_meta": {"title": "Set Block Swap"},
    },
    "80": {
        "inputs": {"model": ["92", 0], "lora": ["56", 0]},
        "class_type": "WanVideoSetLoRAs",
        "_meta": {"title": "Set LoRAs"},
    },
    "38": {
        "inputs": {
            "model_name": "wan_2.1_vae.safetensors",
            "precision": "bf16",
            "use_cpu_cache": False,
            "verbose": False,
        },
        "class_type": "WanVideoVAELoader",
        "_meta": {"title": "WAN VAE Loader"},
    },
    "11": {
        "inputs": {
            "model_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "precision": "bf16",
            "load_device": "offload_device",
            "quantization": "disabled",
        },
        "class_type": "LoadWanVideoT5TextEncoder",
        "_meta": {"title": "T5 Text Encoder"},
    },
    "16": {
        "inputs": {
            "text_encoder": ["11", 0],
            "positive_prompt": "a person is dancing",
            "negative_prompt": _DEFAULT_NEGATIVE_PROMPT,
        },
        "class_type": "WanVideoTextEncode",
        "_meta": {"title": "WAN Text Encode"},
    },
    # CLIP Vision for WAN reference
    "326": {
        "inputs": {"clip_name": "clip_vision_h.safetensors"},
        "class_type": "CLIPVisionLoader",
        "_meta": {"title": "CLIP Vision Loader"},
    },
    "327": {
        "inputs": {
            "clip_vision": ["326", 0],
            "image_1": ["107", 0],
            "strength_1": 1,
            "strength_2": 1,
            "crop": "center",
            "combine_embeds": "average",
            "force_offload": True,
            "tiles": 0,
            "ratio": 0.5,
        },
        "class_type": "WanVideoClipVisionEncode",
        "_meta": {"title": "CLIP Vision Encode"},
    },
    # Empty embeds at half resolution
    "99": {
        "inputs": {
            "width": ["497", 0],
            "height": ["498", 0],
            "num_frames": ["378", 6],
        },
        "class_type": "WanVideoEmptyEmbeds",
        "_meta": {"title": "Empty Embeds"},
    },
    # SCAIL Reference Embeds
    "315": {
        "inputs": {
            "embeds": ["99", 0],
            "vae": ["38", 0],
            "ref_image": ["107", 0],
            "clip_embeds": ["327", 0],
            "ref_strength": 1,
            "start_percent": 0,
            "end_percent": 1,
        },
        "class_type": "WanVideoAddSCAILReferenceEmbeds",
        "_meta": {"title": "SCAIL Reference Embeds"},
    },
    # ===== POSE DETECTION =====
    "335": {
        "inputs": {
            "model_url": "https://github.com/isarandi/nlf/releases/download/v0.2.2/nlf_l_multi_0.2.2.torchscript",
            "use_gpu": True,
        },
        "class_type": "DownloadAndLoadNLFModel",
        "_meta": {"title": "NLF Model"},
    },
    "334": {
        "inputs": {
            "model": ["335", 0],
            "images": ["458", 0],
            "max_detection": -1,
        },
        "class_type": "NLFPredict",
        "_meta": {"title": "NLF Predict"},
    },
    "345": {
        "inputs": {
            "model_name": "vitpose_h_wholebody_model.onnx",
            "det_model": "yolov10m.onnx",
            "execution_provider": "CUDAExecutionProvider",
        },
        "class_type": "OnnxDetectionModelLoader",
        "_meta": {"title": "VitPose Model Loader"},
    },
    "346": {
        "inputs": {"vitpose_model": ["345", 0], "images": ["462", 0]},
        "class_type": "PoseDetectionVitPoseToDWPose",
        "_meta": {"title": "VitPose Video Frames"},
    },
    "347": {
        "inputs": {"vitpose_model": ["345", 0], "images": ["107", 0]},
        "class_type": "PoseDetectionVitPoseToDWPose",
        "_meta": {"title": "VitPose Reference Image"},
    },
    "348": {
        "inputs": {
            "nlf_poses": ["334", 0],
            "dw_poses": ["346", 0],
            "ref_dw_pose": ["347", 0],
            "width": ["497", 0],
            "height": ["498", 0],
            "use_3d_pose": False,
            "use_dw_pose": True,
            "device": "cuda",
            "force_offload": True,
            "renderer": "taichi",
        },
        "class_type": "RenderNLFPoses",
        "_meta": {"title": "Render NLF Poses"},
    },
    # SCAIL Pose Embeds
    "324": {
        "inputs": {
            "embeds": ["315", 0],
            "vae": ["38", 0],
            "pose_images": ["348", 0],
            "pose_strength": 1,
            "start_percent": 0,
            "end_percent": 1,
        },
        "class_type": "WanVideoAddSCAILPoseEmbeds",
        "_meta": {"title": "SCAIL Pose Embeds"},
    },
    # ===== WAN SAMPLER =====
    "351": {
        "inputs": {
            "scheduler": "dpm++_sde",
            "steps": 4,
            "shift": 6,
            "start_step": 0,
            "end_step": -1,
            "force_sigma": True,
        },
        "class_type": "WanVideoSchedulerv2",
        "_meta": {"title": "WAN Scheduler"},
    },
    "353": {
        "inputs": {"riflex_freq_index": 0, "rope_function": "comfy"},
        "class_type": "WanVideoSamplerExtraArgs",
        "_meta": {"title": "Sampler Extra Args"},
    },
    "354": {
        "inputs": {
            "model": ["80", 0],
            "image_embeds": ["324", 0],
            "scheduler": ["351", 0],
            "text_embeds": ["16", 0],
            "extra_args": ["353", 0],
            "cfg": 1,
            "seed": 808804099369531,
            "control_after_generate": "fixed",
            "force_offload": True,
            "batched_cfg": False,
        },
        "class_type": "WanVideoSamplerv2",
        "_meta": {"title": "WAN SCAIL Sampler"},
    },
    # ===== DECODE & OUTPUT =====
    "28": {
        "inputs": {
            "vae": ["38", 0],
            "samples": ["354", 0],
            "enable_vae_tiling": False,
            "tile_x": 272,
            "tile_y": 272,
            "tile_stride_x": 144,
            "tile_stride_y": 128,
            "normalization": "default",
        },
        "class_type": "WanVideoDecode",
        "_meta": {"title": "WAN Video Decode"},
    },
    "139": {
        "inputs": {
            "images": ["28", 0],
            "audio": ["458", 2],
            "frame_rate": 16,
            "loop_count": 0,
            "filename_prefix": "WanVideo_SCAIL",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
        },
        "class_type": "VHS_VideoCombine",
        "_meta": {"title": "Final Video Output"},
    },
}


def _build_scail_workflow(
    video_filename: str,
    image_filename: str,
    prompt: str,
    negative_prompt: Optional[str] = None,
    resolution: int = 832,
) -> dict:
    """Build a WAN SCAIL + Flux Klein ComfyUI workflow with dynamic inputs.

    Args:
        video_filename: Filename for reference video in ComfyUI input dir
        image_filename: Filename for character image in ComfyUI input dir
        prompt: Text prompt describing the desired output
        negative_prompt: Optional negative prompt (uses default if not provided)
        resolution: Target resolution in pixels (320, 640, 832, 960, 1280)

    Returns:
        Complete ComfyUI API-format workflow dict.
    """
    workflow = copy.deepcopy(_SCAIL_WORKFLOW_TEMPLATE)

    # Set dynamic filenames
    workflow["458"]["inputs"]["video"] = video_filename
    workflow["484"]["inputs"]["image"] = image_filename

    # Set prompts
    # Flux Klein positive prompt (for first frame generation)
    workflow["487"]["inputs"]["text"] = prompt

    # WAN positive prompt (for motion description)
    workflow["16"]["inputs"]["positive_prompt"] = prompt

    # Set negative prompts if provided
    if negative_prompt:
        workflow["16"]["inputs"]["negative_prompt"] = negative_prompt

    # Set resolution on resize nodes
    workflow["462"]["inputs"]["width"] = resolution
    workflow["462"]["inputs"]["height"] = resolution
    workflow["107"]["inputs"]["width"] = resolution
    workflow["107"]["inputs"]["height"] = resolution

    # Randomize seeds for each generation
    import random
    workflow["507"]["inputs"]["seed"] = random.randint(0, 2**53)
    workflow["354"]["inputs"]["seed"] = random.randint(0, 2**53)

    return workflow
