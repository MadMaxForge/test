#!/bin/bash
set -e

echo "=== ZIB+ZIT Photo Endpoint - Entrypoint ==="

# Network volume path
VOLUME="/runpod-volume"
MODELS_DIR="${VOLUME}/models"
NODES_DIR="${VOLUME}/custom_nodes_cache"

# ==========================================
# 1. Install custom nodes (cached on volume)
# ==========================================
echo ""
echo "=== Setting up custom nodes ==="

install_node() {
    local repo_url="$1"
    local node_name="$2"
    local cache_dir="${NODES_DIR}/${node_name}"
    local target_dir="/comfyui/custom_nodes/${node_name}"
    
    if [ -d "$target_dir" ]; then
        echo "  [SKIP] ${node_name} already in custom_nodes"
        return 0
    fi
    
    if [ -d "$cache_dir" ]; then
        echo "  [CACHE] ${node_name} - copying from volume cache"
        cp -r "$cache_dir" "$target_dir"
    else
        echo "  [CLONE] ${node_name}..."
        mkdir -p "$NODES_DIR"
        git clone "$repo_url" "$target_dir" 2>&1 | tail -1
        # Cache on volume for next startup
        cp -r "$target_dir" "$cache_dir" 2>/dev/null || true
    fi
    
    # Install requirements
    if [ -f "${target_dir}/requirements.txt" ]; then
        echo "  [PIP] ${node_name} requirements..."
        pip install -r "${target_dir}/requirements.txt" 2>/dev/null || true
    fi
}

install_node "https://github.com/ClownsharkBatwing/RES4LYF.git" "RES4LYF"
install_node "https://github.com/kijai/ComfyUI-KJNodes.git" "ComfyUI-KJNodes"
install_node "https://github.com/rgthree/rgthree-comfy.git" "rgthree-comfy"

# ==========================================
# 2. Update extra_model_paths.yaml
# ==========================================
echo ""
echo "=== Updating model paths ==="

# Add diffusion_models and text_encoders to extra_model_paths if not already there
EXTRA_PATHS="/comfyui/extra_model_paths.yaml"
if ! grep -q "diffusion_models" "$EXTRA_PATHS" 2>/dev/null; then
    printf '  diffusion_models: models/diffusion_models/\n  text_encoders: models/text_encoders/\n' >> "$EXTRA_PATHS"
    echo "  [OK] Added diffusion_models and text_encoders to extra_model_paths.yaml"
else
    echo "  [SKIP] Model paths already configured"
fi

# ==========================================
# 3. Download models to network volume
# ==========================================
echo ""
echo "=== Downloading models to network volume ==="

# Ensure curl is available (pre-installed in base image)
if ! command -v curl &> /dev/null; then
    echo "  [INSTALL] curl..."
    apt-get update -qq && apt-get install -y -qq curl 2>/dev/null
fi

mkdir -p "${MODELS_DIR}/diffusion_models"
mkdir -p "${MODELS_DIR}/text_encoders"
mkdir -p "${MODELS_DIR}/vae"
mkdir -p "${MODELS_DIR}/loras"
mkdir -p "${MODELS_DIR}/checkpoints"

# Validate safetensors file integrity (check header is not truncated)
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

# One-time migration: clear corrupted downloads from previous attempts
VERSION_FILE="${MODELS_DIR}/.download_version"
if [ ! -f "$VERSION_FILE" ] || [ "$(cat "$VERSION_FILE" 2>/dev/null)" != "curl-v3" ]; then
    echo "  [MIGRATE] Clearing potentially corrupted model downloads..."
    rm -f "${MODELS_DIR}/diffusion_models/z_image_bf16.safetensors"
    rm -f "${MODELS_DIR}/diffusion_models/z_image_turbo_bf16.safetensors"
    rm -f "${MODELS_DIR}/text_encoders/qwen_3_4b.safetensors"
    rm -f "${MODELS_DIR}/vae/ae.safetensors"
    rm -f "${MODELS_DIR}/loras/nicegirls_zimagebase.safetensors"
    rm -f "${MODELS_DIR}/loras/Z-Image-Fun-Lora-Distill-8-Steps-2602-ComfyUI.safetensors"
    echo "curl-v3" > "$VERSION_FILE"
    echo "  [OK] Old files cleared, will re-download with curl"
fi

# Download model with curl, validate safetensors integrity, retry on failure
# Args: url dest min_bytes
download_model() {
    local url="$1"
    local dest="$2"
    local min_bytes="${3:-1000000}"
    local filename=$(basename "$dest")
    
    # Check if file exists and is valid
    if [ -f "$dest" ]; then
        local size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -ge "$min_bytes" ]; then
            # Validate safetensors header
            if validate_safetensors "$dest"; then
                echo "  [SKIP] ${filename} ($(numfmt --to=iec $size 2>/dev/null || echo ${size}B))"
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
    
    # Download with curl (handles redirects, shows progress, fails on HTTP errors)
    local attempt=1
    local max_attempts=2
    while [ $attempt -le $max_attempts ]; do
        echo "  [DOWNLOAD] ${filename} (attempt ${attempt}/${max_attempts})..."
        if curl -L -f --progress-bar --retry 3 --retry-delay 5 -o "$dest" "$url"; then
            local size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
            if [ "$size" -ge "$min_bytes" ] && validate_safetensors "$dest"; then
                echo "  [OK] ${filename} ($(numfmt --to=iec $size 2>/dev/null || echo ${size}B))"
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

# Z-Image Base model (12.3 GB)
download_model \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/diffusion_models/z_image_bf16.safetensors" \
    "${MODELS_DIR}/diffusion_models/z_image_bf16.safetensors" \
    12000000000

# Z-Image Turbo model (12.3 GB)
download_model \
    "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors" \
    "${MODELS_DIR}/diffusion_models/z_image_turbo_bf16.safetensors" \
    12000000000

# Qwen 3 4B text encoder (8.04 GB)
download_model \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors" \
    "${MODELS_DIR}/text_encoders/qwen_3_4b.safetensors" \
    8000000000

# VAE ae.safetensors (335 MB) - FLUX AE VAE for Z-Image
download_model \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/vae/ae.safetensors" \
    "${MODELS_DIR}/vae/ae.safetensors" \
    300000000

# NiceGirls ZImageBase LoRA (~170 MB) - renamed to match workflow
download_model \
    "https://huggingface.co/prettyshisya/nicegirls/resolve/main/nicegirls_Zimage.safetensors" \
    "${MODELS_DIR}/loras/nicegirls_zimagebase.safetensors" \
    100000000

# Z-Image Fun Lora Distill 8 Steps (568 MB)
download_model \
    "https://huggingface.co/alibaba-pai/Z-Image-Fun-Lora-Distill/resolve/main/Z-Image-Fun-Lora-Distill-8-Steps-2602-ComfyUI.safetensors" \
    "${MODELS_DIR}/loras/Z-Image-Fun-Lora-Distill-8-Steps-2602-ComfyUI.safetensors" \
    500000000

# Check for custom LoRA
if [ -f "${MODELS_DIR}/loras/LOURTA_000000700.safetensors" ]; then
    echo "  [OK] Custom LoRA LOURTA_000000700.safetensors found"
else
    echo "  [INFO] Custom LoRA LOURTA_000000700.safetensors not found (user will upload later)"
fi

# ==========================================
# 4. Show inventory
# ==========================================
echo ""
echo "=== Network Volume Model Inventory ==="
for dir in diffusion_models text_encoders vae loras checkpoints; do
    echo "  ${dir}/:"
    if [ -d "${MODELS_DIR}/${dir}" ]; then
        ls -lh "${MODELS_DIR}/${dir}/" 2>/dev/null | grep -v total | awk '{print "    " $NF " (" $5 ")"}'
    fi
done
echo ""

# ==========================================
# 5. Start ComfyUI + Handler
# ==========================================
echo "=== Starting ComfyUI + RunPod Handler ==="
exec /start.sh
