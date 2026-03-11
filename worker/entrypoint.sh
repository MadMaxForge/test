#!/bin/bash
# Custom entrypoint for WAN SCAIL + Flux Klein ComfyUI worker
# 1. Updates ComfyUI + critical packages
# 2. Installs custom nodes if not already present
# 3. Downloads all required models to Network Volume (if missing)
# 4. Creates symlinks from Volume to ComfyUI model directories
# 5. Patches handler.py for video output support
# 6. Starts ComfyUI worker via /start.sh
#
# Uses aria2c for fast multi-threaded downloads with auto-resume
# NOTE: No "set -e" — individual failures are handled gracefully

VOLUME_ROOT="/runpod-volume"
VOLUME_MODELS="$VOLUME_ROOT/models"
COMFYUI_MODELS="/comfyui/models"
COMFYUI_NODES="/comfyui/custom_nodes"
COMFYUI_DIR="/comfyui"
DOWNLOAD_ERRORS=0

echo "[entrypoint] === SCAIL Motion Control Worker Starting ==="
echo "[entrypoint] Date: $(date -u)"

# ============================================================
# Step 1: Update ComfyUI (required for flux2 CLIPLoader support)
# Using git pull + pip install (simple, reliable, no hanging)
# ============================================================
echo "[entrypoint] Updating ComfyUI..."
if [ -d "$COMFYUI_DIR/.git" ]; then
    BEFORE_VER=$(cd "$COMFYUI_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[entrypoint] ComfyUI before: $BEFORE_VER"
    cd "$COMFYUI_DIR"
    git checkout -- . 2>/dev/null || true
    git fetch origin 2>&1 | tail -3 || true
    MAIN_BRANCH=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | awk '{print $NF}' || echo "master")
    git merge "origin/$MAIN_BRANCH" --ff-only 2>&1 | tail -5 || true
    pip install -r "$COMFYUI_DIR/requirements.txt" --quiet --no-cache-dir 2>&1 | tail -3 || true
    AFTER_VER=$(cd "$COMFYUI_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[entrypoint] ComfyUI after: $AFTER_VER"
fi

# Upgrade critical packages for flux2/qwen3 support
echo "[entrypoint] Upgrading critical packages..."
pip install --upgrade safetensors transformers tokenizers 2>&1 | tail -5 || true

# Quick version check
python3 -c "
import safetensors, transformers
print(f'[entrypoint] safetensors={safetensors.__version__}, transformers={transformers.__version__}')
try:
    from transformers import Qwen2Tokenizer
    print('[entrypoint] Qwen2Tokenizer: OK')
except Exception as e:
    print(f'[entrypoint] Qwen2Tokenizer: FAILED - {e}')
" 2>&1 || true

echo "[entrypoint] ComfyUI update complete."

# ============================================================
# Step 2: Integrity check on qwen safetensors file
# If file is truncated or comfy_quant data is zeros, delete for re-download
# ============================================================
QWEN_FILE="/runpod-volume/models/text_encoders/qwen_3_8b_fp8mixed.safetensors"
if [ -f "$QWEN_FILE" ]; then
    echo "[entrypoint] Checking qwen file integrity..."
    python3 << 'INTEGRITY_EOF'
import struct, json, os, sys

fpath = '/runpod-volume/models/text_encoders/qwen_3_8b_fp8mixed.safetensors'
EXPECTED_SIZE = 8664848742

actual_size = os.path.getsize(fpath)
print(f'[entrypoint] qwen size: {actual_size} (expected: {EXPECTED_SIZE})')

if actual_size < EXPECTED_SIZE:
    print('[entrypoint] File truncated! Deleting...')
    os.remove(fpath)
    sys.exit(0)

try:
    with open(fpath, 'rb') as f:
        header_size = struct.unpack('<Q', f.read(8))[0]
        header_data = f.read(header_size)
        header = json.loads(header_data.decode('utf-8'))
        data_start = 8 + header_size
        quant_keys = [k for k in header if 'comfy_quant' in k]
        print(f'[entrypoint] comfy_quant tensors: {len(quant_keys)}')
        if quant_keys:
            key = quant_keys[0]
            info = header[key]
            start, end = info['data_offsets']
            size = end - start
            f.seek(data_start + start)
            tensor_bytes = f.read(size)
            if tensor_bytes == b'\x00' * size:
                print('[entrypoint] comfy_quant ALL ZEROS - corrupt! Deleting...')
                os.remove(fpath)
            else:
                try:
                    json.loads(tensor_bytes)
                    print('[entrypoint] comfy_quant data OK')
                except:
                    print('[entrypoint] comfy_quant not valid JSON! Deleting...')
                    os.remove(fpath)
except Exception as e:
    print(f'[entrypoint] Integrity check error: {e}')
INTEGRITY_EOF
fi

# ============================================================
# Step 3: Install aria2 if not present
# ============================================================
if ! command -v aria2c &>/dev/null; then
    echo "[entrypoint] Installing aria2..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends aria2 && rm -rf /var/lib/apt/lists/*
fi

# ============================================================
# Step 4: Install custom nodes
# ============================================================
install_node() {
    local repo_url="$1"
    local dirname="$2"
    local node_path="$COMFYUI_NODES/$dirname"

    if [ -d "$node_path" ] && [ -f "$node_path/__init__.py" -o -d "$node_path/js" -o -f "$node_path/nodes.py" ]; then
        echo "[entrypoint] Node OK: $dirname"
        return 0
    fi

    echo "[entrypoint] Installing node: $dirname ..."
    cd "$COMFYUI_NODES"
    rm -rf "$dirname"
    git clone --depth 1 "$repo_url" "$dirname" 2>&1 | tail -3
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

echo "[entrypoint] Custom nodes ready."

# ============================================================
# Step 5: Patch handler.py for video output (VHS_VideoCombine uses 'gifs' key)
# ============================================================
if ! grep -q 'gifs' /handler.py 2>/dev/null; then
    echo "[entrypoint] Patching handler.py for video output..."
    python3 << 'PATCH_EOF'
import re

handler_path = '/handler.py'
with open(handler_path) as f:
    content = f.read()

patch_code = """
            # [SCAIL patch] Map VHS_VideoCombine 'gifs' output to 'images' for video support
            if "gifs" in node_output and "images" not in node_output:
                node_output["images"] = node_output["gifs"]
"""

pattern = r'(\n)([ \t]*)(if "images" in node_output:)'
match = re.search(pattern, content)
if match:
    indent = match.group(2)
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
    print('[entrypoint] Patched handler.py: added gifs->images mapping')
else:
    print('[entrypoint] WARNING: Could not find patch target in handler.py')
PATCH_EOF
fi

# ============================================================
# Step 6: Download models helpers
# ============================================================
validate_safetensors() {
    local filepath="$1"
    python3 -c "
import struct, sys
try:
    with open(sys.argv[1], 'rb') as f:
        raw = f.read(8)
        if len(raw) < 8:
            sys.exit(1)
        header_size = struct.unpack('<Q', raw)[0]
        if header_size < 2 or header_size > 200000000:
            sys.exit(1)
        f.read(min(header_size, 4096)).decode('utf-8')
    sys.exit(0)
except:
    sys.exit(1)
" "$filepath" 2>/dev/null
    return $?
}

download_model() {
    local url="$1"
    local dest_dir="$2"
    local filename="$3"
    local min_size="$4"
    local dest_path="$dest_dir/$filename"

    mkdir -p "$dest_dir"

    if [ -f "$dest_path" ] && [ $(stat -c%s "$dest_path" 2>/dev/null || echo 0) -ge "$min_size" ]; then
        if echo "$filename" | grep -q '\.safetensors$'; then
            if ! validate_safetensors "$dest_path"; then
                echo "[entrypoint] CORRUPT: $filename -- re-downloading"
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

    rm -f "$dest_path"
    echo "[entrypoint] Downloading $filename ..."
    aria2c -x 16 -s 16 -k 20M \
        --auto-file-renaming=false \
        --allow-overwrite=true \
        -d "$dest_dir" -o "$filename" \
        --console-log-level=notice \
        --summary-interval=30 \
        --timeout=300 \
        --max-tries=3 \
        "$url"

    if [ $? -eq 0 ] && [ -f "$dest_path" ] && [ $(stat -c%s "$dest_path" 2>/dev/null || echo 0) -ge "$min_size" ]; then
        echo "[entrypoint] Downloaded: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
    else
        echo "[entrypoint] WARNING: $filename download failed!"
        rm -f "$dest_path"
        DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))
    fi
}

download_all_models() {
    DOWNLOAD_ERRORS=0

    DIFF_DIR="$VOLUME_MODELS/diffusion_models"
    TE_DIR="$VOLUME_MODELS/text_encoders"
    CV_DIR="$VOLUME_MODELS/clip_vision"
    VAE_DIR="$VOLUME_MODELS/vae"
    LORA_DIR="$VOLUME_MODELS/loras"
    DET_DIR="$VOLUME_MODELS/detection"

    download_model \
        "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SCAIL/Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors" \
        "$DIFF_DIR" "Wan21-14B-SCAIL-preview_fp8_e4m3fn_scaled_KJ.safetensors" 14000000000

    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
        "$TE_DIR" "umt5_xxl_fp8_e4m3fn_scaled.safetensors" 4000000000

    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
        "$CV_DIR" "clip_vision_h.safetensors" 1200000000

    download_model \
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors" \
        "$VAE_DIR" "wan_2.1_vae.safetensors" 400000000

    download_model \
        "https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v/resolve/main/loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" \
        "$LORA_DIR" "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" 700000000

    download_model \
        "https://huggingface.co/Kijai/vitpose_comfy/resolve/main/onnx/vitpose_h_wholebody_model.onnx" \
        "$DET_DIR" "vitpose_h_wholebody_model.onnx" 100000

    download_model \
        "https://huggingface.co/Kijai/vitpose_comfy/resolve/main/onnx/vitpose_h_wholebody_data.bin" \
        "$DET_DIR" "vitpose_h_wholebody_data.bin" 2000000000

    download_model \
        "https://huggingface.co/Wan-AI/Wan2.2-Animate-14B/resolve/main/process_checkpoint/det/yolov10m.onnx" \
        "$DET_DIR" "yolov10m.onnx" 1000000

    download_model \
        "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8/resolve/main/flux-2-klein-9b-fp8.safetensors" \
        "$DIFF_DIR" "flux-2-klein-9b-fp8.safetensors" 9000000000

    # Fallback: ModelScope if HuggingFace failed (gated repo)
    if [ ! -f "$DIFF_DIR/flux-2-klein-9b-fp8.safetensors" ] || \
       [ $(stat -c%s "$DIFF_DIR/flux-2-klein-9b-fp8.safetensors" 2>/dev/null || echo 0) -lt 9000000000 ]; then
        echo "[entrypoint] Trying ModelScope for Flux Klein..."
        download_model \
            "https://modelscope.cn/models/black-forest-labs/FLUX.2-klein-9b-fp8/resolve/master/flux-2-klein-9b-fp8.safetensors" \
            "$DIFF_DIR" "flux-2-klein-9b-fp8.safetensors" 9000000000
    fi

    download_model \
        "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors" \
        "$TE_DIR" "qwen_3_8b_fp8mixed.safetensors" 8600000000

    download_model \
        "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/vae/flux2-vae.safetensors" \
        "$VAE_DIR" "flux2-vae.safetensors" 200000000

    if [ $DOWNLOAD_ERRORS -gt 0 ]; then
        echo "[entrypoint] WARNING: $DOWNLOAD_ERRORS download(s) failed."
    fi
    echo "[entrypoint] All models checked/downloaded."
    sync_symlinks
}

# ============================================================
# sync_symlinks: Volume -> ComfyUI dirs
# ============================================================
sync_symlinks() {
    if [ -d "$VOLUME_MODELS" ]; then
        echo "[entrypoint] Creating symlinks..."
        for vol_dir in "$VOLUME_MODELS"/*/; do
            [ -d "$vol_dir" ] || continue
            dirname=$(basename "$vol_dir")
            target_dir="$COMFYUI_MODELS/$dirname"
            mkdir -p "$target_dir"
            for item in "$vol_dir"*; do
                [ -e "$item" ] || continue
                itemname=$(basename "$item")
                ln -sf "$item" "$target_dir/$itemname"
            done
        done
        echo "[entrypoint] Symlinks created."
    fi
}

# ============================================================
# Step 7: Symlink existing models first (instant)
# ============================================================
sync_symlinks

# ============================================================
# Step 8: Download models (blocking - must complete before handler)
# ============================================================
if [ -d "$VOLUME_ROOT" ]; then
    echo "[entrypoint] Starting model downloads (blocking)..."
    download_all_models 2>&1 | tee /var/log/model-downloads.log
    echo "[entrypoint] Model downloads complete."
else
    echo "[entrypoint] WARNING: No Network Volume at $VOLUME_ROOT"
fi

# ============================================================
# Step 9: Start worker via /start.sh
# ============================================================
echo "[entrypoint] Setup complete. Starting worker..."
exec /start.sh
