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

# Install aria2 if not available
if ! command -v aria2c &> /dev/null; then
    echo "  [INSTALL] aria2..."
    apt-get update -qq && apt-get install -y -qq aria2 2>/dev/null
fi

mkdir -p "${MODELS_DIR}/diffusion_models"
mkdir -p "${MODELS_DIR}/text_encoders"
mkdir -p "${MODELS_DIR}/vae"
mkdir -p "${MODELS_DIR}/loras"
mkdir -p "${MODELS_DIR}/checkpoints"

download_if_missing() {
    local url="$1"
    local dest="$2"
    local filename=$(basename "$dest")
    
    if [ -f "$dest" ]; then
        local size=$(stat -f%z "$dest" 2>/dev/null || stat -c%s "$dest" 2>/dev/null || echo "0")
        if [ "$size" -gt 1000000 ]; then
            echo "  [SKIP] ${filename} already exists ($(numfmt --to=iec $size 2>/dev/null || echo ${size}B))"
            return 0
        fi
        echo "  [REDOWNLOAD] ${filename} exists but too small, re-downloading..."
        rm -f "$dest"
    fi
    
    echo "  [DOWNLOAD] ${filename}..."
    aria2c -x 16 -s 16 --max-tries=3 --retry-wait=5 \
        --file-allocation=none --console-log-level=warn \
        -d "$(dirname "$dest")" \
        -o "${filename}" \
        "$url" 2>&1 | tail -3
    
    if [ -f "$dest" ]; then
        echo "  [OK] ${filename}"
    else
        echo "  [WARN] ${filename} download may have failed"
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
# IMPORTANT: Shared volume may have WAN VAE from InfiniteTalk cached as ae.safetensors.
# aria2c parallel downloads corrupt this file through HF xet-bridge CDN.
# Using huggingface_hub (built into ComfyUI) for reliable download.
VAE_DEST="${MODELS_DIR}/vae/ae.safetensors"
VAE_EXPECTED_SIZE=335304388
if [ -f "$VAE_DEST" ]; then
    VAE_SIZE=$(stat -c%s "$VAE_DEST" 2>/dev/null || echo "0")
    if [ "$VAE_SIZE" -ne "$VAE_EXPECTED_SIZE" ]; then
        echo "  [FIX] ae.safetensors is wrong model or corrupted (${VAE_SIZE} bytes, expected ${VAE_EXPECTED_SIZE}). Deleting..."
        rm -f "$VAE_DEST"
    else
        echo "  [SKIP] ae.safetensors correct ($(numfmt --to=iec $VAE_SIZE 2>/dev/null || echo ${VAE_SIZE}B))"
    fi
fi
if [ ! -f "$VAE_DEST" ]; then
    echo "  [DOWNLOAD] ae.safetensors via huggingface_hub..."
    python3 -c "
import shutil, os
try:
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(repo_id='Comfy-Org/z_image', filename='split_files/vae/ae.safetensors')
    dest = '${VAE_DEST}'
    shutil.copy2(path, dest)
    size = os.path.getsize(dest)
    print(f'  [OK] ae.safetensors downloaded via huggingface_hub ({size} bytes)')
except Exception as e:
    print(f'  [WARN] huggingface_hub failed: {e}, trying requests...')
    try:
        import requests
        url = 'https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/vae/ae.safetensors'
        r = requests.get(url, stream=True, allow_redirects=True, timeout=300)
        r.raise_for_status()
        dest = '${VAE_DEST}'
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1048576):
                f.write(chunk)
        size = os.path.getsize(dest)
        print(f'  [OK] ae.safetensors downloaded via requests ({size} bytes)')
    except Exception as e2:
        print(f'  [ERROR] All download methods failed: {e2}')
" || echo "  [WARN] VAE download script failed, continuing anyway..."
    if [ -f "$VAE_DEST" ]; then
        VAE_SIZE=$(stat -c%s "$VAE_DEST" 2>/dev/null || echo "0")
        if [ "$VAE_SIZE" -lt 300000000 ]; then
            echo "  [ERROR] ae.safetensors too small (${VAE_SIZE} bytes), removing..."
            rm -f "$VAE_DEST"
        fi
    fi
fi
# Clean up old renamed copies from previous fix attempts
rm -f "${MODELS_DIR}/vae/ae_zimage.safetensors" 2>/dev/null || true
rm -f "${MODELS_DIR}/vae/ae_flux_zimage.safetensors" 2>/dev/null || true

# NiceGirls ZImageBase LoRA (~170 MB) - renamed to match workflow
if [ ! -f "${MODELS_DIR}/loras/nicegirls_zimagebase.safetensors" ]; then
    echo "  [DOWNLOAD] nicegirls_zimagebase.safetensors..."
    aria2c -x 16 -s 16 --max-tries=3 --retry-wait=5 \
        --file-allocation=none --console-log-level=warn \
        -d "${MODELS_DIR}/loras" \
        -o "nicegirls_zimagebase.safetensors" \
        "https://huggingface.co/prettyshisya/nicegirls/resolve/main/nicegirls_Zimage.safetensors" 2>&1 | tail -3
    echo "  [OK] nicegirls_zimagebase.safetensors"
else
    echo "  [SKIP] nicegirls_zimagebase.safetensors already exists"
fi

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
