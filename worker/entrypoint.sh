#!/bin/bash
# Custom entrypoint: download missing models + symlink Network Volume models
# The Network Volume has models at /runpod-volume/runpod-slim/ComfyUI/models/
# But the official worker expects them at /comfyui/models/

VOLUME_MODELS="/runpod-volume/runpod-slim/ComfyUI/models"
COMFYUI_MODELS="/comfyui/models"

# ============================================================
# Step 1: Download missing diffusion models to Network Volume
# These are large models that must be on the volume for the
# WAN 2.1 InfiniteTalk workflow to work.
# ============================================================
DIFFUSION_DIR="$VOLUME_MODELS/diffusion_models"

if [ -d "$VOLUME_MODELS" ]; then
    mkdir -p "$DIFFUSION_DIR"

    # WAN 2.1 I2V 14B model (Q8_0 quantized, ~18GB)
    WAN_I2V="$DIFFUSION_DIR/wan2.1-i2v-14b-480p-Q8_0.gguf"
    if [ ! -f "$WAN_I2V" ] || [ $(stat -c%s "$WAN_I2V" 2>/dev/null || echo 0) -lt 18000000000 ]; then
        echo "[entrypoint] Downloading wan2.1-i2v-14b-480p-Q8_0.gguf from HuggingFace..."
        wget -q --show-progress -O "$WAN_I2V" \
            "https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf"
        if [ $? -eq 0 ]; then
            echo "[entrypoint] WAN I2V model downloaded successfully ($(stat -c%s "$WAN_I2V" | numfmt --to=iec) bytes)"
        else
            echo "[entrypoint] WARNING: WAN I2V model download failed!"
        fi
    else
        echo "[entrypoint] WAN I2V model already present ($(stat -c%s "$WAN_I2V" | numfmt --to=iec))"
    fi

    # InfiniteTalk Single model (Q8 quantized, ~2.6GB)
    INFTALK="$DIFFUSION_DIR/Wan2_1-InfiniteTalk_Single_Q8.gguf"
    if [ ! -f "$INFTALK" ] || [ $(stat -c%s "$INFTALK" 2>/dev/null || echo 0) -lt 2600000000 ]; then
        echo "[entrypoint] Downloading Wan2_1-InfiniteTalk_Single_Q8.gguf from HuggingFace..."
        wget -q --show-progress -O "$INFTALK" \
            "https://huggingface.co/kijai/WAN2.1-InfiniteTalk-GGUF/resolve/main/Wan2_1-InfiniteTalk_Single_Q8.gguf"
        if [ $? -eq 0 ]; then
            echo "[entrypoint] InfiniteTalk model downloaded successfully ($(stat -c%s "$INFTALK" | numfmt --to=iec) bytes)"
        else
            echo "[entrypoint] WARNING: InfiniteTalk model download failed!"
        fi
    else
        echo "[entrypoint] InfiniteTalk model already present ($(stat -c%s "$INFTALK" | numfmt --to=iec))"
    fi
fi

# ============================================================
# Step 2: Create symlinks from Network Volume to ComfyUI paths
# ============================================================
if [ -d "$VOLUME_MODELS" ]; then
    echo "[entrypoint] Network Volume found at $VOLUME_MODELS, creating symlinks..."
    
    # Iterate over each model subdirectory on the volume
    for vol_dir in "$VOLUME_MODELS"/*/; do
        [ -d "$vol_dir" ] || continue
        dirname=$(basename "$vol_dir")
        target_dir="$COMFYUI_MODELS/$dirname"
        
        # Create target directory if it doesn't exist
        mkdir -p "$target_dir"
        
        # Symlink each file and subdirectory from volume to comfyui models
        for item in "$vol_dir"*; do
            [ -e "$item" ] || continue
            itemname=$(basename "$item")
            # Skip placeholder files
            if [[ "$itemname" == put_* ]]; then
                continue
            fi
            # Create symlink (overwrite if exists)
            ln -sf "$item" "$target_dir/$itemname"
            echo "[entrypoint]   Linked: $dirname/$itemname"
        done
    done
    
    echo "[entrypoint] Model symlinks created successfully."
else
    echo "[entrypoint] No Network Volume models found at $VOLUME_MODELS"
fi

# ============================================================
# Step 3: Start ComfyUI worker
# ============================================================
echo "[entrypoint] Starting ComfyUI worker..."
exec /start.sh
