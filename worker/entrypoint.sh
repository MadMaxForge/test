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
# The base image uses comfy-cli to install ComfyUI into /comfyui with
# a virtual env at /opt/venv. We MUST use comfy-cli to update properly
# so that all dependencies (including new ones for flux2/qwen3) are resolved.
# ============================================================
echo "[entrypoint] Updating ComfyUI to latest version..."
COMFYUI_DIR="/comfyui"

# Log current version before update
if [ -d "$COMFYUI_DIR/.git" ]; then
    BEFORE_VER=$(cd "$COMFYUI_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[entrypoint] ComfyUI commit before update: $BEFORE_VER"
fi

# Method 1: Use comfy-cli (preferred - properly resolves all dependencies)
if command -v comfy &>/dev/null; then
    echo "[entrypoint] Using comfy-cli to update ComfyUI..."
    comfy --workspace "$COMFYUI_DIR" update --version latest 2>&1 | tail -20
    echo "[entrypoint] comfy-cli update completed (exit code: $?)"
# Method 2: Fallback to git pull + pip install
elif [ -d "$COMFYUI_DIR/.git" ]; then
    echo "[entrypoint] comfy-cli not found, falling back to git pull..."
    cd "$COMFYUI_DIR"
    git checkout -- . 2>/dev/null || true
    git pull --ff-only 2>&1 | tail -5
    pip install -r "$COMFYUI_DIR/requirements.txt" --quiet --no-cache-dir 2>&1 | tail -3 || true
else
    echo "[entrypoint] WARNING: No update method available, using base image version"
fi

# Log version after update
if [ -d "$COMFYUI_DIR/.git" ]; then
    AFTER_VER=$(cd "$COMFYUI_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[entrypoint] ComfyUI commit after update: $AFTER_VER"
fi

# Upgrade critical Python packages for flux2/qwen3 support
# - safetensors: newer versions handle quantization metadata correctly
# - transformers: Qwen2Tokenizer needed for flux2 Klein text encoder
# The utf-32-be decode error can be caused by outdated transformers
echo "[entrypoint] Upgrading critical Python packages..."
pip install --upgrade safetensors transformers tokenizers 2>&1 | tail -5 || true

# Verify critical modules and versions
python3 -c "
import sys, os, struct, json, traceback
sys.path.insert(0, '/comfyui')

# Print versions
try:
    import safetensors; print(f'[entrypoint] safetensors version: {safetensors.__version__}')
except: print('[entrypoint] safetensors: not importable')

try:
    import torch; print(f'[entrypoint] torch version: {torch.__version__}')
except: print('[entrypoint] torch: not importable')

try:
    import transformers; print(f'[entrypoint] transformers version: {transformers.__version__}')
except: print('[entrypoint] transformers: not importable')

try:
    from transformers import Qwen2Tokenizer
    print('[entrypoint] Qwen2Tokenizer import: OK')
except Exception as e:
    print(f'[entrypoint] WARNING: Qwen2Tokenizer import FAILED: {e}')

# Check qwen25_tokenizer directory
tok_dir = '/comfyui/comfy/text_encoders/qwen25_tokenizer'
if os.path.isdir(tok_dir):
    files = os.listdir(tok_dir)
    print(f'[entrypoint] qwen25_tokenizer dir: {files}')
    for f in files:
        fp = os.path.join(tok_dir, f)
        sz = os.path.getsize(fp)
        print(f'[entrypoint]   {f}: {sz} bytes')
        # Quick check: try reading first 100 bytes
        with open(fp, 'rb') as fh:
            head = fh.read(100)
            print(f'[entrypoint]   {f} starts with: {head[:50]}')
else:
    print(f'[entrypoint] WARNING: qwen25_tokenizer directory NOT FOUND at {tok_dir}')

# Check ComfyUI git version
import subprocess
try:
    ver = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd='/comfyui', text=True).strip()
    print(f'[entrypoint] ComfyUI git commit: {ver}')
except: print('[entrypoint] ComfyUI git: unknown')

# Check if CLIPType.FLUX2 exists
try:
    import comfy.sd
    if hasattr(comfy.sd, 'CLIPType') and hasattr(comfy.sd.CLIPType, 'FLUX2'):
        print(f'[entrypoint] CLIPType.FLUX2 = {comfy.sd.CLIPType.FLUX2}')
    else:
        print('[entrypoint] WARNING: CLIPType.FLUX2 NOT FOUND - ComfyUI is too old!')
except Exception as e:
    print(f'[entrypoint] WARNING: cannot check CLIPType: {e}')

# Pre-flight check: try loading qwen safetensors header
QWEN_PATH = '/runpod-volume/models/text_encoders/qwen_3_8b_fp8mixed.safetensors'
if os.path.exists(QWEN_PATH):
    try:
        import safetensors.torch
        # Try loading just the metadata (fast, doesn't load tensors)
        with safetensors.safe_open(QWEN_PATH, framework='pt', device='cpu') as f:
            metadata = f.metadata()
            print(f'[entrypoint] qwen metadata keys: {list(metadata.keys()) if metadata else \"None\"}')
            if metadata:
                for k, v in metadata.items():
                    print(f'[entrypoint]   {k}: {len(v)} chars')
            keys = list(f.keys())
            print(f'[entrypoint] qwen tensor count: {len(keys)}')
            print(f'[entrypoint] qwen first 5 tensors: {keys[:5]}')
            # Diagnostic: check comfy_quant tensor dtype and contents
            quant_keys = [k for k in keys if 'comfy_quant' in k]
            print(f'[entrypoint] comfy_quant tensors: {len(quant_keys)}')
            if quant_keys:
                t = f.get_tensor(quant_keys[0])
                print(f'[entrypoint] comfy_quant[0] key: {quant_keys[0]}')
                print(f'[entrypoint] comfy_quant[0] dtype: {t.dtype}, shape: {t.shape}, numel: {t.numel()}')
                raw = t.numpy().tobytes()
                print(f'[entrypoint] comfy_quant[0] tobytes len: {len(raw)}, first 40: {raw[:40]}')
                # Try extracting values as list of ints
                vals = t.tolist()
                print(f'[entrypoint] comfy_quant[0] tolist first 40: {vals[:40]}')
                byte_vals = bytes([v & 0xFF for v in vals])
                print(f'[entrypoint] comfy_quant[0] as bytes: {byte_vals[:60]}')
                try:
                    decoded = byte_vals.decode('utf-8')
                    import json as json_mod
                    parsed = json_mod.loads(decoded)
                    print(f'[entrypoint] comfy_quant[0] parsed JSON: {parsed}')
                except Exception as e2:
                    print(f'[entrypoint] comfy_quant[0] decode error: {e2}')
        print('[entrypoint] qwen safetensors pre-flight: OK')
    except Exception as e:
        print(f'[entrypoint] qwen safetensors pre-flight FAILED: {e}')
        traceback.print_exc()
else:
    print(f'[entrypoint] qwen file not yet downloaded (will be downloaded later)')
" 2>&1 || true
echo "[entrypoint] ComfyUI update complete."

# ============================================================
# INTEGRITY CHECK: Verify comfy_quant tensor data in qwen safetensors file
# The utf-32-be error is caused by corrupted/truncated file where the
# comfy_quant tensors (at ~7.7GB offset) are all zeros instead of JSON data.
# If corrupt, delete and re-download.
# ============================================================
QWEN_FILE="/runpod-volume/models/text_encoders/qwen_3_8b_fp8mixed.safetensors"
if [ -f "$QWEN_FILE" ]; then
    echo "[entrypoint] Verifying qwen safetensors file integrity (comfy_quant data)..."
    python3 << 'INTEGRITY_EOF'
import struct, json, os, sys

fpath = '/runpod-volume/models/text_encoders/qwen_3_8b_fp8mixed.safetensors'
EXPECTED_SIZE = 8664848742  # bytes from HuggingFace

actual_size = os.path.getsize(fpath)
print(f'[entrypoint] qwen file size: {actual_size} (expected: {EXPECTED_SIZE})')

if actual_size < EXPECTED_SIZE:
    print(f'[entrypoint] ERROR: File is truncated! {actual_size} < {EXPECTED_SIZE}')
    print(f'[entrypoint] Deleting corrupt file for re-download...')
    os.remove(fpath)
    sys.exit(0)

# Read header to find comfy_quant tensor location
with open(fpath, 'rb') as f:
    header_size = struct.unpack('<Q', f.read(8))[0]
    header_data = f.read(header_size)
    header = json.loads(header_data.decode('utf-8'))
    data_start = 8 + header_size

    # Find first comfy_quant tensor
    quant_keys = [k for k in header if 'comfy_quant' in k]
    print(f'[entrypoint] Found {len(quant_keys)} comfy_quant tensors')

    if quant_keys:
        key = quant_keys[0]
        info = header[key]
        start, end = info['data_offsets']
        size = end - start
        file_offset = data_start + start
        print(f'[entrypoint] Checking {key}: dtype={info["dtype"]}, offset={file_offset}, size={size}')

        # Read actual tensor bytes
        f.seek(file_offset)
        tensor_bytes = f.read(size)
        print(f'[entrypoint] Tensor bytes: {tensor_bytes}')

        if tensor_bytes == b'\x00' * size:
            print(f'[entrypoint] ERROR: comfy_quant data is ALL ZEROS - file is corrupt!')
            print(f'[entrypoint] Deleting corrupt file for re-download...')
            os.remove(fpath)
        else:
            try:
                parsed = json.loads(tensor_bytes)
                print(f'[entrypoint] comfy_quant data OK: {parsed}')
            except Exception as e:
                print(f'[entrypoint] ERROR: comfy_quant data is not valid JSON: {e}')
                print(f'[entrypoint] Raw bytes: {tensor_bytes[:100]}')
                print(f'[entrypoint] Deleting corrupt file for re-download...')
                os.remove(fpath)
INTEGRITY_EOF
fi

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
