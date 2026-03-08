#!/bin/bash
# Custom entrypoint for WAN 2.1 InfiniteTalk ComfyUI worker
# 1. Downloads all required models to Network Volume (if missing)
# 2. Creates symlinks from Volume to ComfyUI model directories
# 3. Starts ComfyUI worker
#
# Uses aria2c for fast multi-threaded downloads with auto-resume
# NOTE: No "set -e" — individual download failures are handled gracefully
# so subsequent downloads and worker startup are not blocked.

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
# Model names MUST match exactly what the ComfyUI workflow expects.
# ============================================================
if [ -d "$VOLUME_ROOT" ]; then
    echo "[entrypoint] Network Volume found at $VOLUME_ROOT"
    echo "[entrypoint] Checking and downloading models..."

    # --- diffusion_models ---
    DIFF_DIR="$VOLUME_MODELS/diffusion_models"

    # WAN 2.1 I2V 14B base model (GGUF Q8, ~16 GB)
    download_model \
        "https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf" \
        "$DIFF_DIR" "wan2.1-i2v-14b-480p-Q8_0.gguf" 15000000000

    # InfiniteTalk Single Q8 GGUF (~2.65 GB)
    # Source: Kijai/WanVideo_comfy_GGUF (public, apache-2.0)
    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf" \
        "$DIFF_DIR" "Wan2_1-InfiniteTalk_Single_Q8.gguf" 2600000000

    # --- text_encoders ---
    TE_DIR="$VOLUME_MODELS/text_encoders"

    # UMT5-XXL bf16 text encoder (~4.7 GB)
    # Workflow node 241 expects: "umt5-xxl-enc-bf16.safetensors"
    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors" \
        "$TE_DIR" "umt5-xxl-enc-bf16.safetensors" 4000000000

    # --- clip_vision ---
    CV_DIR="$VOLUME_MODELS/clip_vision"

    # CLIP Vision H (~1.8 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
        "$CV_DIR" "clip_vision_h.safetensors" 1200000000

    # --- vae ---
    VAE_DIR="$VOLUME_MODELS/vae"

    # WAN 2.1 VAE bf16 (~480 MB)
    # Workflow node 129 expects: "Wan2_1_VAE_bf16.safetensors"
    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors" \
        "$VAE_DIR" "Wan2_1_VAE_bf16.safetensors" 400000000

    # --- loras ---
    LORA_DIR="$VOLUME_MODELS/loras"

    # LightX2V I2V 14B distillation LoRA rank64 (~738 MB)
    # Workflow node 138 expects: "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors"
    # Source file has different name, so we download with -o to rename it.
    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors" \
        "$LORA_DIR" "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" 700000000

    # --- wav2vec (TencentGameMate/chinese-wav2vec2-base) ---
    # The DownloadAndLoadWav2VecModel node auto-downloads from HuggingFace
    # at runtime, but we pre-cache it to avoid cold-start delay.
    W2V_DIR="$VOLUME_MODELS/transformers/TencentGameMate--chinese-wav2vec2-base"

    download_model \
        "https://huggingface.co/TencentGameMate/chinese-wav2vec2-base/resolve/main/pytorch_model.bin" \
        "$W2V_DIR" "pytorch_model.bin" 360000000

    # wav2vec config files (small)
    for cfg_file in config.json preprocessor_config.json; do
        if [ ! -f "$W2V_DIR/$cfg_file" ]; then
            aria2c -x 4 -s 4 --auto-file-renaming=false --allow-overwrite=true \
                -c -d "$W2V_DIR" -o "$cfg_file" \
                "https://huggingface.co/TencentGameMate/chinese-wav2vec2-base/resolve/main/$cfg_file" 2>/dev/null || true
        fi
    done

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
