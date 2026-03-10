#!/bin/bash
# Custom entrypoint for WAN SCAIL + Flux Klein ComfyUI worker
# 1. Downloads all required models to Network Volume (if missing)
# 2. Creates symlinks from Volume to ComfyUI model directories
# 3. Starts ComfyUI worker
#
# Uses aria2c for fast multi-threaded downloads with auto-resume
# NOTE: No "set -e" — individual download failures are handled gracefully

VOLUME_ROOT="/runpod-volume"
VOLUME_MODELS="$VOLUME_ROOT/models"
COMFYUI_MODELS="/comfyui/models"
DOWNLOAD_ERRORS=0

# ============================================================
# Helper: download a model file if missing or incomplete
# Usage: download_model <url> <dest_dir> <filename> <min_size>
# ============================================================
download_model() {
    local url="$1"
    local dest_dir="$2"
    local filename="$3"
    local min_size="$4"
    local dest_path="$dest_dir/$filename"

    mkdir -p "$dest_dir"

    if [ -f "$dest_path" ] && [ $(stat -c%s "$dest_path" 2>/dev/null || echo 0) -ge "$min_size" ]; then
        echo "[entrypoint] OK: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
        return 0
    fi

    echo "[entrypoint] Downloading $filename ..."
    aria2c -x 16 -s 16 -k 20M \
        --auto-file-renaming=false \
        --allow-overwrite=true \
        -c -d "$dest_dir" -o "$filename" \
        --console-log-level=notice \
        --summary-interval=10 \
        "$url"

    if [ $? -eq 0 ] && [ -f "$dest_path" ] && [ $(stat -c%s "$dest_path" 2>/dev/null || echo 0) -ge "$min_size" ]; then
        echo "[entrypoint] Downloaded $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
    else
        echo "[entrypoint] WARNING: $filename download may have failed!"
        DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))
    fi
}

# ============================================================
# Step 1: Download all required models to Network Volume
# ============================================================
if [ -d "$VOLUME_ROOT" ]; then
    echo "[entrypoint] Network Volume found at $VOLUME_ROOT"
    echo "[entrypoint] Checking and downloading models..."

    # ===== WAN SCAIL Models =====

    # --- diffusion_models ---
    DIFF_DIR="$VOLUME_MODELS/diffusion_models"

    # WAN 2.1 SCAIL 14B FP8 (~15 GB)
    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SCAIL/Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors" \
        "$DIFF_DIR" "Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors" 14000000000

    # --- text_encoders ---
    TE_DIR="$VOLUME_MODELS/text_encoders"

    # UMT5-XXL FP8 scaled text encoder (~5 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
        "$TE_DIR" "umt5_xxl_fp8_e4m3fn_scaled.safetensors" 4000000000

    # --- clip_vision ---
    CV_DIR="$VOLUME_MODELS/clip_vision"

    # CLIP Vision H (~1.8 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
        "$CV_DIR" "clip_vision_h.safetensors" 1200000000

    # --- vae ---
    VAE_DIR="$VOLUME_MODELS/vae"

    # WAN 2.1 VAE (~480 MB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors" \
        "$VAE_DIR" "wan_2.1_vae.safetensors" 400000000

    # --- loras ---
    LORA_DIR="$VOLUME_MODELS/loras"

    # LightX2V speed LoRA rank64 (~738 MB)
    download_model \
        "https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v/resolve/main/loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" \
        "$LORA_DIR" "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" 700000000

    # ===== Detection Models =====
    DET_DIR="$VOLUME_MODELS/detection"

    # VitPose H wholebody ONNX model
    download_model \
        "https://huggingface.co/Kijai/vitpose_comfy/resolve/main/onnx/vitpose_h_wholebody_model.onnx" \
        "$DET_DIR" "vitpose_h_wholebody_model.onnx" 1000000

    # YOLOv10m detection model
    download_model \
        "https://huggingface.co/Wan-AI/Wan2.2-Animate-14B/resolve/main/process_checkpoint/det/yolov10m.onnx" \
        "$DET_DIR" "yolov10m.onnx" 1000000

    # ===== Flux Klein Models =====

    # Flux Klein 9B FP8 diffusion model (~9.5 GB)
    # Try HuggingFace first (gated), fall back to ModelScope mirror if unavailable
    download_model \
        "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8/resolve/main/flux-2-klein-9b-fp8.safetensors" \
        "$DIFF_DIR" "flux-2-klein-9b-fp8.safetensors" 9000000000

    # Fallback: if HuggingFace download failed (gated repo), try ModelScope
    if [ ! -f "$DIFF_DIR/flux-2-klein-9b-fp8.safetensors" ] || \
       [ $(stat -c%s "$DIFF_DIR/flux-2-klein-9b-fp8.safetensors" 2>/dev/null || echo 0) -lt 9000000000 ]; then
        echo "[entrypoint] HuggingFace download failed for Flux Klein, trying ModelScope..."
        download_model \
            "https://modelscope.cn/models/black-forest-labs/FLUX.2-klein-9b-fp8/resolve/master/flux-2-klein-9b-fp8.safetensors" \
            "$DIFF_DIR" "flux-2-klein-9b-fp8.safetensors" 9000000000
    fi

    # Flux Klein text encoder - Qwen 3 8B FP8 mixed (~4.5 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors" \
        "$TE_DIR" "qwen_3_8b_fp8mixed.safetensors" 4000000000

    # Flux2 VAE (~250 MB)
    download_model \
        "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/vae/flux2-vae.safetensors" \
        "$VAE_DIR" "flux2-vae.safetensors" 200000000

    if [ $DOWNLOAD_ERRORS -gt 0 ]; then
        echo "[entrypoint] WARNING: $DOWNLOAD_ERRORS download(s) may have failed. Check logs above."
    fi
    echo "[entrypoint] All models checked/downloaded."
else
    echo "[entrypoint] WARNING: No Network Volume at $VOLUME_ROOT"
fi

# ============================================================
# Step 2: Symlink Volume models to ComfyUI model directories
# ============================================================
if [ -d "$VOLUME_MODELS" ]; then
    echo "[entrypoint] Creating symlinks from Volume to ComfyUI..."

    for vol_dir in "$VOLUME_MODELS"/*/; do
        [ -d "$vol_dir" ] || continue
        dirname=$(basename "$vol_dir")
        target_dir="$COMFYUI_MODELS/$dirname"
        mkdir -p "$target_dir"

        for item in "$vol_dir"*; do
            [ -e "$item" ] || continue
            itemname=$(basename "$item")
            ln -sf "$item" "$target_dir/$itemname"
            echo "[entrypoint]   Linked: $dirname/$itemname"
        done
    done

    echo "[entrypoint] Symlinks created."
fi

# ============================================================
# Step 3: Start ComfyUI worker
# ============================================================
echo "[entrypoint] Starting ComfyUI worker..."
exec /start.sh
