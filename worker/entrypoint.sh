#!/bin/bash
# Custom entrypoint for WAN 2.1 InfiniteTalk ComfyUI worker
# 1. Downloads all required models to Network Volume (if missing)
# 2. Creates symlinks from Volume to ComfyUI model directories
# 3. Starts ComfyUI worker
#
# Uses aria2c for fast multi-threaded downloads with auto-resume

set -e

VOLUME_ROOT="/runpod-volume"
VOLUME_MODELS="$VOLUME_ROOT/models"
COMFYUI_MODELS="/comfyui/models"

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
    fi
}

# ============================================================
# Step 1: Download all required models to Network Volume
# ============================================================
if [ -d "$VOLUME_ROOT" ]; then
    echo "[entrypoint] Network Volume found at $VOLUME_ROOT"
    echo "[entrypoint] Checking and downloading models..."

    # --- diffusion_models (~20.6 GB total) ---
    DIFF_DIR="$VOLUME_MODELS/diffusion_models"

    download_model \
        "https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf" \
        "$DIFF_DIR" "wan2.1-i2v-14b-480p-Q8_0.gguf" 18000000000

    download_model \
        "https://huggingface.co/kijai/WAN2.1-InfiniteTalk-GGUF/resolve/main/Wan2_1-InfiniteTalk_Single_Q8.gguf" \
        "$DIFF_DIR" "Wan2_1-InfiniteTalk_Single_Q8.gguf" 2600000000

    # --- text_encoders (~11 GB) ---
    TE_DIR="$VOLUME_MODELS/text_encoders"

    download_model \
        "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/umt5_xxl_fp8_e4m3fn.safetensors" \
        "$TE_DIR" "umt5_xxl_fp8_e4m3fn.safetensors" 11000000000

    # --- clip_vision (~1.3 GB) ---
    CV_DIR="$VOLUME_MODELS/clip_vision"

    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
        "$CV_DIR" "clip_vision_h.safetensors" 1300000000

    # --- vae (~250 MB) ---
    VAE_DIR="$VOLUME_MODELS/vae"

    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors" \
        "$VAE_DIR" "wan_2.1_vae.safetensors" 200000000

    # --- loras (~740 MB) ---
    LORA_DIR="$VOLUME_MODELS/loras"

    download_model \
        "https://huggingface.co/kijai/WAN2.1-InfiniteTalk-GGUF/resolve/main/Wan21_InfiniTalk_LoRA.safetensors" \
        "$LORA_DIR" "Wan21_InfiniTalk_LoRA.safetensors" 700000000

    # --- transformers/wav2vec2-base-960h (~360 MB) ---
    W2V_DIR="$VOLUME_MODELS/transformers/wav2vec2-base-960h"

    download_model \
        "https://huggingface.co/facebook/wav2vec2-base-960h/resolve/main/pytorch_model.bin" \
        "$W2V_DIR" "pytorch_model.bin" 360000000

    # wav2vec config files (small)
    for cfg_file in config.json preprocessor_config.json tokenizer_config.json vocab.json special_tokens_map.json; do
        if [ ! -f "$W2V_DIR/$cfg_file" ]; then
            aria2c -x 4 -s 4 --auto-file-renaming=false --allow-overwrite=true \
                -c -d "$W2V_DIR" -o "$cfg_file" \
                "https://huggingface.co/facebook/wav2vec2-base-960h/resolve/main/$cfg_file" 2>/dev/null || true
        fi
    done

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
