#!/bin/bash
# Custom entrypoint for WAN SCAIL + Flux Klein ComfyUI worker
# 0. Installs custom nodes if not already present (supports base image deployment)
# 1. Downloads all required models to Network Volume (if missing)
# 2. Creates symlinks from Volume to ComfyUI model directories
# 3. Patches handler.py for video output support
# 4. Starts ComfyUI worker
#
# Uses aria2c for fast multi-threaded downloads with auto-resume
# NOTE: No "set -e" — individual download failures are handled gracefully

VOLUME_ROOT="/runpod-volume"
VOLUME_MODELS="$VOLUME_ROOT/models"
COMFYUI_MODELS="/comfyui/models"
COMFYUI_NODES="/comfyui/custom_nodes"
DOWNLOAD_ERRORS=0

# ============================================================
# Step 0: Install custom nodes if not already present
# (Supports deployment with base image when Docker build is unavailable)
# ============================================================
install_node() {
    local repo_url="$1"
    local dirname="$2"
    local node_path="$COMFYUI_NODES/$dirname"

    if [ -d "$node_path" ] && [ -f "$node_path/__init__.py" -o -d "$node_path/js" -o -f "$node_path/nodes.py" ]; then
        echo "[entrypoint] Node OK: $dirname"
        return 0
    fi

    echo "[entrypoint] Installing custom node: $dirname ..."
    cd "$COMFYUI_NODES"
    rm -rf "$dirname"
    git clone --depth 1 "$repo_url" "$dirname" 2>&1
    if [ -f "$node_path/requirements.txt" ]; then
        pip install -r "$node_path/requirements.txt" --no-cache-dir 2>&1 | tail -3
    fi
    if [ -f "$node_path/install.py" ]; then
        cd "$node_path" && python install.py 2>&1 | tail -3 || true
    fi
    echo "[entrypoint] Installed: $dirname"
}

echo "[entrypoint] Checking custom nodes..."
mkdir -p "$COMFYUI_NODES"

# ============================================================
# Update ComfyUI to latest version (required for flux2 CLIPLoader support)
# The base image may have an older version that doesn't support flux2 type
# ============================================================
echo "[entrypoint] Updating ComfyUI to latest version..."
COMFYUI_DIR="/comfyui"
if [ -d "$COMFYUI_DIR/.git" ]; then
    cd "$COMFYUI_DIR"
    # Log current version before update
    BEFORE_VER=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[entrypoint] ComfyUI version before update: $BEFORE_VER"
    # Reset any local changes that might block pull
    git checkout -- . 2>/dev/null || true
    git pull --ff-only 2>&1 | tail -5
    AFTER_VER=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[entrypoint] ComfyUI version after update: $AFTER_VER"
    pip install -r "$COMFYUI_DIR/requirements.txt" --quiet --no-cache-dir 2>&1 | tail -3 || true
else
    echo "[entrypoint] WARNING: /comfyui is not a git repo, trying pip upgrade..."
    pip install --upgrade comfyui 2>&1 | tail -5 || true
fi
# Log the ComfyUI version for debugging
python3 -c "import importlib; m=importlib.import_module('comfy.cli_args'); print('[entrypoint] ComfyUI module loaded OK')" 2>/dev/null || true
echo "[entrypoint] ComfyUI updated."

# Install aria2 if not present (base image may not have it)
if ! command -v aria2c &>/dev/null; then
    echo "[entrypoint] Installing aria2..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends aria2 && rm -rf /var/lib/apt/lists/*
fi

install_node "https://github.com/kijai/ComfyUI-WanVideoWrapper.git" "ComfyUI-WanVideoWrapper"
install_node "https://github.com/kijai/ComfyUI-SCAIL-Pose.git" "ComfyUI-SCAIL-Pose"
install_node "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git" "ComfyUI-VideoHelperSuite"
install_node "https://github.com/kijai/ComfyUI-KJNodes.git" "ComfyUI-KJNodes"
install_node "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git" "ComfyUI-Impact-Pack"
install_node "https://github.com/yolain/ComfyUI-Easy-Use.git" "ComfyUI-Easy-Use"
install_node "https://github.com/kijai/ComfyUI-WanAnimatePreprocess.git" "ComfyUI-WanAnimatePreprocess"
install_node "https://github.com/Fannovel16/comfyui_controlnet_aux.git" "comfyui_controlnet_aux"
install_node "https://github.com/ClownsharkBatwing/RES4LYF.git" "RES4LYF"

# Install onnxruntime-gpu if not present
python3 -c "import onnxruntime" 2>/dev/null || {
    echo "[entrypoint] Installing onnxruntime-gpu..."
    pip install onnxruntime-gpu --no-cache-dir 2>&1 | tail -3 || pip install onnxruntime --no-cache-dir 2>&1 | tail -3
}

# Patch handler.py for video output support (VHS_VideoCombine uses 'gifs' key)
# The base handler only checks for 'images' in node_output, but VHS outputs under 'gifs'
# Also, temp outputs are skipped by the handler, so we need save_output=True in workflow
if ! grep -q 'gifs' /handler.py 2>/dev/null; then
    echo "[entrypoint] Patching handler.py for video output (gifs -> images)..."
    python3 << 'PATCH_EOF'
import re

handler_path = '/handler.py'
with open(handler_path) as f:
    content = f.read()

# Strategy: Before the 'if "images" in node_output:' check, add gifs->images mapping
# This handles VHS_VideoCombine which outputs videos under 'gifs' key
patch_code = '''
            # [SCAIL patch] Map VHS_VideoCombine 'gifs' output to 'images' for video support
            if "gifs" in node_output and "images" not in node_output:
                node_output["images"] = node_output["gifs"]
'''

# Find the pattern: 'if "images" in node_output:' with any indentation
pattern = r'(\n)([ \t]*)(if "images" in node_output:)'
match = re.search(pattern, content)
if match:
    indent = match.group(2)
    # Insert patch before the if statement, with matching indentation
    patch_lines = []
    for line in patch_code.strip().split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            patch_lines.append(f'{indent}{stripped}')
        elif stripped.startswith('if'):
            patch_lines.append(f'{indent}{stripped}')
        elif stripped.startswith('node_output'):
            patch_lines.append(f'{indent}    {stripped}')
        else:
            patch_lines.append(f'{indent}{stripped}')
    
    replacement = '\n' + '\n'.join(patch_lines) + '\n' + match.group(2) + match.group(3)
    content = content[:match.start()] + replacement + content[match.end():]
    
    with open(handler_path, 'w') as f:
        f.write(content)
    print(f'[entrypoint] Patched handler.py: added gifs->images mapping')
else:
    print('[entrypoint] WARNING: Could not find patch target in handler.py')
PATCH_EOF
fi

echo "[entrypoint] Custom nodes ready."

# ============================================================
# Helper: validate safetensors file header
# Returns 0 if valid, 1 if corrupt/invalid
# ============================================================
validate_safetensors() {
    local filepath="$1"
    python3 -c "
import struct, json, sys
try:
    with open(sys.argv[1], 'rb') as f:
        raw = f.read(8)
        if len(raw) < 8:
            print('[validate] File too small for safetensors header')
            sys.exit(1)
        header_size = struct.unpack('<Q', raw)[0]
        if header_size < 2 or header_size > 200_000_000:
            print(f'[validate] Suspicious header size: {header_size}')
            sys.exit(1)
        header_bytes = f.read(min(header_size, 4096))
        # Try to decode as UTF-8 (safetensors uses UTF-8 JSON headers)
        header_bytes.decode('utf-8')
    print('[validate] Header OK')
    sys.exit(0)
except Exception as e:
    print(f'[validate] CORRUPT: {e}')
    sys.exit(1)
" "$filepath" 2>&1
    return $?
}

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

    # Check size
    if [ -f "$dest_path" ] && [ $(stat -c%s "$dest_path" 2>/dev/null || echo 0) -ge "$min_size" ]; then
        # For safetensors files, also validate the header
        if echo "$filename" | grep -q '\.safetensors$'; then
            if ! validate_safetensors "$dest_path"; then
                echo "[entrypoint] CORRUPT safetensors detected: $filename — deleting for re-download"
                rm -f "$dest_path"
            else
                echo "[entrypoint] OK: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
                return 0
            fi
        else
            echo "[entrypoint] OK: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
            return 0
        fi
    fi

    # Delete any partial/corrupt file before downloading
    rm -f "$dest_path"

    echo "[entrypoint] Downloading $filename ..."
    aria2c -x 16 -s 16 -k 20M \
        --auto-file-renaming=false \
        --allow-overwrite=true \
        -d "$dest_dir" -o "$filename" \
        --console-log-level=notice \
        --summary-interval=10 \
        "$url"

    if [ $? -eq 0 ] && [ -f "$dest_path" ] && [ $(stat -c%s "$dest_path" 2>/dev/null || echo 0) -ge "$min_size" ]; then
        echo "[entrypoint] Downloaded $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
    else
        echo "[entrypoint] WARNING: $filename download may have failed!"
        rm -f "$dest_path"
        DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))
    fi
}

# ============================================================
# download_all_models: downloads all required models to volume
# Called in FOREGROUND (blocking) to ensure files are complete
# ============================================================
download_all_models() {
    DOWNLOAD_ERRORS=0

    # ===== WAN SCAIL Models =====
    DIFF_DIR="$VOLUME_MODELS/diffusion_models"
    TE_DIR="$VOLUME_MODELS/text_encoders"
    CV_DIR="$VOLUME_MODELS/clip_vision"
    VAE_DIR="$VOLUME_MODELS/vae"
    LORA_DIR="$VOLUME_MODELS/loras"
    DET_DIR="$VOLUME_MODELS/detection"

    # WAN 2.1 SCAIL 14B FP8 (~15 GB)
    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SCAIL/Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors" \
        "$DIFF_DIR" "Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors" 14000000000

    # UMT5-XXL FP8 scaled text encoder (~5 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
        "$TE_DIR" "umt5_xxl_fp8_e4m3fn_scaled.safetensors" 4000000000

    # CLIP Vision H (~1.8 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
        "$CV_DIR" "clip_vision_h.safetensors" 1200000000

    # WAN 2.1 VAE (~480 MB)
    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors" \
        "$VAE_DIR" "wan_2.1_vae.safetensors" 400000000

    # LightX2V speed LoRA rank64 (~738 MB)
    download_model \
        "https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v/resolve/main/loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" \
        "$LORA_DIR" "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" 700000000

    # VitPose H wholebody ONNX model (420 KB model + 2.55 GB data file)
    download_model \
        "https://huggingface.co/Kijai/vitpose_comfy/resolve/main/onnx/vitpose_h_wholebody_model.onnx" \
        "$DET_DIR" "vitpose_h_wholebody_model.onnx" 100000

    # VitPose H wholebody external data (required by ONNX model)
    download_model \
        "https://huggingface.co/Kijai/vitpose_comfy/resolve/main/onnx/vitpose_h_wholebody_data.bin" \
        "$DET_DIR" "vitpose_h_wholebody_data.bin" 2000000000

    # YOLOv10m detection model
    download_model \
        "https://huggingface.co/Wan-AI/Wan2.2-Animate-14B/resolve/main/process_checkpoint/det/yolov10m.onnx" \
        "$DET_DIR" "yolov10m.onnx" 1000000

    # Flux Klein 9B FP8 diffusion model (~9.5 GB)
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

    # Flux Klein text encoder - Qwen 3 8B FP8 mixed (~8.07 GB)
    download_model \
        "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors" \
        "$TE_DIR" "qwen_3_8b_fp8mixed.safetensors" 8600000000

    # Flux2 VAE (~250 MB)
    download_model \
        "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/vae/flux2-vae.safetensors" \
        "$VAE_DIR" "flux2-vae.safetensors" 200000000

    if [ $DOWNLOAD_ERRORS -gt 0 ]; then
        echo "[entrypoint] WARNING: $DOWNLOAD_ERRORS download(s) may have failed. Check logs above."
    fi
    echo "[entrypoint] All models checked/downloaded."

    # Re-create symlinks after downloads complete
    sync_symlinks
}

# ============================================================
# sync_symlinks: create symlinks from Volume to ComfyUI dirs
# ============================================================
sync_symlinks() {
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
}

# ============================================================
# Step 1: Symlink any existing models (instant, from prior runs)
# ============================================================
sync_symlinks

# ============================================================
# Step 2: Download models in FOREGROUND (blocking)
# Models MUST be fully downloaded before handler starts,
# otherwise safetensors files may be truncated causing
# 'utf-32-be' decode errors in CLIPLoader
# First run will take ~10-20 min; subsequent runs are instant
# ============================================================
if [ -d "$VOLUME_ROOT" ]; then
    echo "[entrypoint] Network Volume found at $VOLUME_ROOT"
    echo "[entrypoint] Starting model downloads (blocking until complete)..."
    download_all_models 2>&1 | tee /var/log/model-downloads.log
    echo "[entrypoint] Model downloads complete."
else
    echo "[entrypoint] WARNING: No Network Volume at $VOLUME_ROOT — models must already be in container"
fi

# ============================================================
# Step 3: Delegate to the original /start.sh from the base image
# /start.sh handles: libtcmalloc, ComfyUI startup, handler startup
# This is the proven startup sequence from runpod/worker-comfyui
# ============================================================
echo "[entrypoint] Setup complete. Delegating to /start.sh..."
exec /start.sh
