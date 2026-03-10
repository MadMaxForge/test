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

# Ensure wget is available (installed in Dockerfile)
if ! command -v wget &> /dev/null; then
    echo "  [INSTALL] wget..."
    apt-get update -qq && apt-get install -y -qq wget 2>/dev/null
fi

mkdir -p "${MODELS_DIR}/diffusion_models"
mkdir -p "${MODELS_DIR}/text_encoders"
mkdir -p "${MODELS_DIR}/vae"
mkdir -p "${MODELS_DIR}/loras"
mkdir -p "${MODELS_DIR}/checkpoints"

# One-time migration: if models were downloaded with aria2c (corrupts through HF xet-bridge CDN),
# delete them and re-download with wget (single connection, handles redirects reliably).
VERSION_FILE="${MODELS_DIR}/.download_version"
if [ ! -f "$VERSION_FILE" ] || [ "$(cat "$VERSION_FILE" 2>/dev/null)" != "wget-v2" ]; then
    echo "  [MIGRATE] Clearing models downloaded with aria2c (corrupted through xet-bridge CDN)..."
    rm -f "${MODELS_DIR}/diffusion_models/z_image_bf16.safetensors"
    rm -f "${MODELS_DIR}/diffusion_models/z_image_turbo_bf16.safetensors"
    rm -f "${MODELS_DIR}/text_encoders/qwen_3_4b.safetensors"
    rm -f "${MODELS_DIR}/vae/ae.safetensors"
    rm -f "${MODELS_DIR}/loras/nicegirls_zimagebase.safetensors"
    rm -f "${MODELS_DIR}/loras/Z-Image-Fun-Lora-Distill-8-Steps-2602-ComfyUI.safetensors"
    echo "wget-v2" > "$VERSION_FILE"
    echo "  [OK] Old files cleared, will re-download with wget"
fi

download_if_missing() {
    local url="$1"
    local dest="$2"
    local filename=$(basename "$dest")
    
    if [ -f "$dest" ]; then
        local size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -gt 1000000 ]; then
            echo "  [SKIP] ${filename} already exists ($(numfmt --to=iec $size 2>/dev/null || echo ${size}B))"
            return 0
        fi
        echo "  [REDOWNLOAD] ${filename} exists but too small, re-downloading..."
        rm -f "$dest"
    fi
    
    echo "  [DOWNLOAD] ${filename}..."
    wget -q --show-progress -O "$dest" "$url" || true
    
    if [ -f "$dest" ]; then
        local size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -lt 1000000 ]; then
            echo "  [ERROR] ${filename} too small after download (${size} bytes), removing..."
            rm -f "$dest"
        else
            echo "  [OK] ${filename} ($(numfmt --to=iec $size 2>/dev/null || echo ${size}B))"
        fi
    else
        echo "  [ERROR] ${filename} download failed!"
    fi
}

# Z-Image Base model (12.3 GB)
download_if_missing \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/diffusion_models/z_image_bf16.safetensors" \
    "${MODELS_DIR}/diffusion_models/z_image_bf16.safetensors"

# Z-Image Turbo model (12.3 GB)
download_if_missing \
    "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors" \
    "${MODELS_DIR}/diffusion_models/z_image_turbo_bf16.safetensors"

# Qwen 3 4B text encoder (8.04 GB)
download_if_missing \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors" \
    "${MODELS_DIR}/text_encoders/qwen_3_4b.safetensors"

# VAE ae.safetensors (335 MB) - FLUX AE VAE for Z-Image
download_if_missing \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/vae/ae.safetensors" \
    "${MODELS_DIR}/vae/ae.safetensors"

# NiceGirls ZImageBase LoRA (~170 MB) - renamed to match workflow
download_if_missing \
    "https://huggingface.co/prettyshisya/nicegirls/resolve/main/nicegirls_Zimage.safetensors" \
    "${MODELS_DIR}/loras/nicegirls_zimagebase.safetensors"

# Z-Image Fun Lora Distill 8 Steps (568 MB)
download_if_missing \
    "https://huggingface.co/alibaba-pai/Z-Image-Fun-Lora-Distill/resolve/main/Z-Image-Fun-Lora-Distill-8-Steps-2602-ComfyUI.safetensors" \
    "${MODELS_DIR}/loras/Z-Image-Fun-Lora-Distill-8-Steps-2602-ComfyUI.safetensors"

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
