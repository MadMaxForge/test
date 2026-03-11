"""Z-Image Turbo workflow builder — constructs ComfyUI API-format workflow for RunPod."""

import random
from typing import Optional


def build_zimage_workflow(
    prompt: str,
    width: int = 1088,
    height: int = 1920,
    seed: Optional[int] = None,
    lora_name: str = "w1man.safetensors",
    lora_strength: float = 1.0,
    detail_lora_strength: float = 0.5,
    slider_lora_strength: float = 0.45,
) -> dict:
    """Build a z-image-turbo ComfyUI workflow in API format.

    The workflow chain:
      UNETLoader -> LoRA(DetailDaemon) -> LoRA(Slider) -> LoRA(character)
      -> ModelSamplingAuraFlow -> KSampler -> VAEDecode -> SaveImage

    Args:
        prompt: Full text prompt (must include trigger word like 'w1man').
        width: Image width (default 1088 for portrait).
        height: Image height (default 1920 for portrait).
        seed: Random seed. If None, generates random.
        lora_name: Character LoRA filename.
        lora_strength: Character LoRA strength (0.0 - 2.0).
        detail_lora_strength: DetailDaemon LoRA strength.
        slider_lora_strength: Breast slider LoRA strength.

    Returns:
        dict: ComfyUI API-format workflow ready for RunPod.
    """
    if seed is None:
        seed = random.randint(1, 2**53)

    workflow: dict = {
        # Step 1: Load base model
        "28": {
            "inputs": {
                "unet_name": "z_image_turbo_bf16.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
            "_meta": {"title": "Load Diffusion Model"},
        },
        # Step 1b: Load text encoder
        "30": {
            "inputs": {
                "clip_name": "qwen_3_4b.safetensors",
                "type": "lumina2",
                "device": "default",
            },
            "class_type": "CLIPLoader",
            "_meta": {"title": "Load CLIP"},
        },
        # Step 1c: Load VAE
        "29": {
            "inputs": {
                "vae_name": "ae.safetensors",
            },
            "class_type": "VAELoader",
            "_meta": {"title": "Load VAE"},
        },
        # LoRA chain: DetailDaemon -> Slider -> Character
        "40": {
            "inputs": {
                "model": ["28", 0],
                "lora_name": "REDZ15_DetailDaemonZ_lora_v1.1.safetensors",
                "strength_model": detail_lora_strength,
            },
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": "LoRA - DetailDaemon"},
        },
        "37": {
            "inputs": {
                "model": ["40", 0],
                "lora_name": "Z-Breast-Slider.safetensors",
                "strength_model": slider_lora_strength,
            },
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": "LoRA - Slider"},
        },
        "36": {
            "inputs": {
                "model": ["37", 0],
                "lora_name": lora_name,
                "strength_model": lora_strength,
            },
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": "LoRA - Character"},
        },
        # Model sampling
        "11": {
            "inputs": {
                "model": ["36", 0],
                "shift": 3,
            },
            "class_type": "ModelSamplingAuraFlow",
            "_meta": {"title": "ModelSamplingAuraFlow"},
        },
        # Text encoding (prompt)
        "27": {
            "inputs": {
                "clip": ["30", 0],
                "text": prompt,
            },
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "CLIP Text Encode (Prompt)"},
        },
        # Negative conditioning (zeroed out)
        "33": {
            "inputs": {
                "conditioning": ["27", 0],
            },
            "class_type": "ConditioningZeroOut",
            "_meta": {"title": "Conditioning Zero Out"},
        },
        # Empty latent image
        "13": {
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
            "class_type": "EmptySD3LatentImage",
            "_meta": {"title": "Empty Latent Image"},
        },
        # KSampler
        "3": {
            "inputs": {
                "model": ["11", 0],
                "positive": ["27", 0],
                "negative": ["33", 0],
                "latent_image": ["13", 0],
                "seed": seed,
                "control_after_generate": "randomize",
                "steps": 10,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
            },
            "class_type": "KSampler",
            "_meta": {"title": "KSampler"},
        },
        # VAE Decode
        "8": {
            "inputs": {
                "samples": ["3", 0],
                "vae": ["29", 0],
            },
            "class_type": "VAEDecode",
            "_meta": {"title": "VAE Decode"},
        },
        # Save Image
        "9": {
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": "z-image",
            },
            "class_type": "SaveImage",
            "_meta": {"title": "Save Image"},
        },
    }

    return workflow


def build_prompt_with_guide(
    scene_description: str,
    brand_guide: dict,
) -> str:
    """Combine scene description with brand guide character data.

    Ensures the trigger word is at the start and character appearance is included.
    """
    trigger = brand_guide.get("character", {}).get("trigger_word", "w1man")

    # Make sure prompt starts with trigger word
    if not scene_description.strip().lower().startswith(f"a {trigger}"):
        scene_description = f"A {trigger}, {scene_description}"

    return scene_description
