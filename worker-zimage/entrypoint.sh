#!/bin/bash
# Z-Image Turbo endpoint entrypoint
# 1. Downloads all required models to Network Volume (if missing)
# 2. Downloads LoRA files from Google Drive (if missing)
# 3. Creates symlinks from Volume to ComfyUI model directories
# 4. Starts ComfyUI worker

echo "=== Z-Image Turbo Endpoint - Entrypoint ==="

VOLUME="/runpod-volume"
MODELS_DIR="${VOLUME}/models"
COMFYUI_MODELS="/comfyui/models"

# ============================================================
# Helper: validate safetensors file header
# ============================================================
validate_safetensors() {
    local filepath="$1"
    python3 -c "
import struct, sys, os
try:
    with open(sys.argv[1], 'rb') as f:
        data = f.read(8)
        if len(data) < 8:
            print('  [VALIDATE] Header too short'); sys.exit(1)
        header_size = struct.unpack('<Q', data)[0]
        file_size = os.path.getsize(sys.argv[1])
        if header_size > file_size:
            print(f'  [VALIDATE] Truncated: header claims {header_size} but file is {file_size}')
            sys.exit(1)
        if header_size < 10:
            print(f'  [VALIDATE] Invalid header size: {header_size}')
            sys.exit(1)
        print(f'  [VALIDATE] OK ({file_size} bytes, header {header_size})')
except Exception as e:
    print(f'  [VALIDATE] Error: {e}'); sys.exit(1)
" "$filepath"
}

# ============================================================
# Helper: download model with curl, validate, retry on failure
# Args: url dest min_bytes
# ============================================================
download_model() {
    local url="$1"
    local dest="$2"
    local min_bytes="${3:-1000000}"
    local filename
    filename=$(basename "$dest")

    mkdir -p "$(dirname "$dest")"

    # Check if file exists and is valid
    if [ -f "$dest" ]; then
        local size
        size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -ge "$min_bytes" ]; then
            if validate_safetensors "$dest"; then
                echo "  [SKIP] ${filename} ($(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B"))"
                return 0
            else
                echo "  [CORRUPT] ${filename} failed validation, re-downloading..."
                rm -f "$dest"
            fi
        else
            echo "  [TRUNCATED] ${filename} too small (${size} < ${min_bytes}), re-downloading..."
            rm -f "$dest"
        fi
    fi

    # Download with curl
    local attempt=1
    local max_attempts=2
    while [ $attempt -le $max_attempts ]; do
        echo "  [DOWNLOAD] ${filename} (attempt ${attempt}/${max_attempts})..."
        if curl -L -f --progress-bar --retry 3 --retry-delay 5 -o "$dest" "$url"; then
            local size
            size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
            if [ "$size" -ge "$min_bytes" ] && validate_safetensors "$dest"; then
                echo "  [OK] ${filename} ($(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B"))"
                return 0
            fi
            echo "  [FAILED] ${filename} downloaded but invalid (${size} bytes)"
            rm -f "$dest"
        else
            echo "  [FAILED] ${filename} curl failed (attempt ${attempt})"
            rm -f "$dest"
        fi
        attempt=$((attempt + 1))
    done
    echo "  [ERROR] ${filename} all download attempts failed!"
    return 1
}

# ============================================================
# Helper: download LoRA from Google Drive via gdown
# Args: gdrive_file_id dest min_bytes
# ============================================================
download_gdrive_lora() {
    local file_id="$1"
    local dest="$2"
    local min_bytes="${3:-1000000}"
    local filename
    filename=$(basename "$dest")

    mkdir -p "$(dirname "$dest")"

    if [ -f "$dest" ]; then
        local size
        size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -ge "$min_bytes" ] && validate_safetensors "$dest"; then
            echo "  [SKIP] ${filename} ($(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B"))"
            return 0
        else
            echo "  [CORRUPT/SMALL] ${filename}, re-downloading..."
            rm -f "$dest"
        fi
    fi

    echo "  [DOWNLOAD] ${filename} from Google Drive (id: ${file_id})..."
    if gdown "${file_id}" -O "$dest" 2>&1; then
        local size
        size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -ge "$min_bytes" ] && validate_safetensors "$dest"; then
            echo "  [OK] ${filename} ($(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B"))"
            return 0
        fi
        echo "  [FAILED] ${filename} downloaded but invalid"
        rm -f "$dest"
    else
        echo "  [FAILED] ${filename} gdown failed"
        rm -f "$dest"
    fi
    return 1
}

# ============================================================
# Step 1: Update extra_model_paths.yaml (runtime, idempotent)
# ============================================================
echo ""
echo "=== Updating model paths ==="
EXTRA_PATHS="/comfyui/extra_model_paths.yaml"
if ! grep -q "diffusion_models" "$EXTRA_PATHS" 2>/dev/null; then
    printf '  diffusion_models: models/diffusion_models/\n  text_encoders: models/text_encoders/\n' >> "$EXTRA_PATHS"
    echo "  [OK] Added diffusion_models and text_encoders to extra_model_paths.yaml"
else
    echo "  [SKIP] Model paths already configured"
fi

# ============================================================
# Step 2: Download models to Network Volume
# ============================================================
if [ -d "$VOLUME" ]; then
    echo ""
    echo "=== Downloading models to Network Volume ==="

    mkdir -p "${MODELS_DIR}/diffusion_models"
    mkdir -p "${MODELS_DIR}/text_encoders"
    mkdir -p "${MODELS_DIR}/vae"
    mkdir -p "${MODELS_DIR}/loras"

    # Z-Image Turbo diffusion model (~12.3 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors" \
        "${MODELS_DIR}/diffusion_models/z_image_turbo_bf16.safetensors" \
        12000000000

    # Qwen 3 4B text encoder (~8 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors" \
        "${MODELS_DIR}/text_encoders/qwen_3_4b.safetensors" \
        8000000000

    # VAE ae.safetensors (~335 MB)
    download_model \
        "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors" \
        "${MODELS_DIR}/vae/ae.safetensors" \
        300000000

    # --- LoRA files from Google Drive ---
    # Folder: https://drive.google.com/drive/folders/1sUu4B9oqaYUShGhOF8ErT8algD_i_jMz

    # REDZ15_DetailDaemonZ_lora_v1.1.safetensors (133.7 MB) - detail enhancement
    download_gdrive_lora \
        "1im5eDzuPhL34uSwRlVt-4nhG9mJ_ralT" \
        "${MODELS_DIR}/loras/REDZ15_DetailDaemonZ_lora_v1.1.safetensors" \
        100000000

    # Z-Breast-Slider.safetensors (20.3 MB)
    download_gdrive_lora \
        "1BKVe7fhCC2s5dqXzgYgop-83SPBz5tmy" \
        "${MODELS_DIR}/loras/Z-Breast-Slider.safetensors" \
        15000000

    # w1man.safetensors (162.2 MB) - custom character
    download_gdrive_lora \
        "1DvMsvfy-IkJg56fQ-Df-wzL9UjOoHPkk" \
        "${MODELS_DIR}/loras/w1man.safetensors" \
        100000000

    # ==========================================
    # Step 3: Show inventory
    # ==========================================
    echo ""
    echo "=== Network Volume Model Inventory ==="
    for dir in diffusion_models text_encoders vae loras; do
        echo "  ${dir}/:"
        if [ -d "${MODELS_DIR}/${dir}" ]; then
            ls -lh "${MODELS_DIR}/${dir}/" 2>/dev/null | grep -v total | awk '{print "    " $NF " (" $5 ")"}'
        fi
    done
    echo ""
else
    echo "[entrypoint] WARNING: No Network Volume at $VOLUME"
fi

# ============================================================
# Step 4: Symlink Volume models to ComfyUI model directories
# ============================================================
if [ -d "$MODELS_DIR" ]; then
    echo "=== Creating symlinks from Volume to ComfyUI ==="
    for vol_dir in "$MODELS_DIR"/*/; do
        [ -d "$vol_dir" ] || continue
        dirname=$(basename "$vol_dir")
        target_dir="$COMFYUI_MODELS/$dirname"
        mkdir -p "$target_dir"

        for item in "$vol_dir"*; do
            [ -e "$item" ] || continue
            itemname=$(basename "$item")
            ln -sf "$item" "$target_dir/$itemname"
            echo "  Linked: $dirname/$itemname"
        done
    done
    echo "  Symlinks created."
fi

# ============================================================
# Step 5: Start ComfyUI worker
# ============================================================
echo ""
echo "=== Starting ComfyUI + RunPod Handler ==="
exec /start.sh
