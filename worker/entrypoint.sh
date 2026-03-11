#!/bin/bash
# SCAIL Motion Control Worker - Entrypoint v2
#
# KEY DESIGN:
#   1. /start.sh runs in BACKGROUND -> worker becomes 'ready' immediately
#   2. Heavy setup runs in FOREGROUND -> keeps container alive until complete
#   3. 'wait' on /start.sh -> container stays alive indefinitely after setup
#
# This avoids TWO problems:
#   - RunPod init timeout (worker reports ready quickly)
#   - Container killed during setup (foreground process keeps it alive)
#
# On subsequent boots with cached volume: everything symlinked in <30s, instant start

VOLUME_ROOT="/runpod-volume"
VOLUME_MODELS="$VOLUME_ROOT/models"
VOLUME_NODES="$VOLUME_ROOT/custom_nodes"
COMFYUI_MODELS="/comfyui/models"
COMFYUI_NODES="/comfyui/custom_nodes"
COMFYUI_DIR="/comfyui"

echo "[entrypoint] === SCAIL Motion Control Worker Starting ==="
echo "[entrypoint] Date: $(date -u)"

# ============================================================
# PHASE 1: Quick setup (<30 seconds)
# Symlink cached nodes + models from volume, patch handler
# ============================================================

# 1a. Symlink cached custom nodes from volume (instant)
mkdir -p "$COMFYUI_NODES"
NODES_LINKED=0
if [ -d "$VOLUME_NODES" ]; then
    for node_dir in "$VOLUME_NODES"/*/; do
        [ -d "$node_dir" ] || continue
        dirname=$(basename "$node_dir")
        ln -sf "$node_dir" "$COMFYUI_NODES/$dirname"
        NODES_LINKED=$((NODES_LINKED + 1))
    done
    echo "[entrypoint] Linked $NODES_LINKED cached custom nodes from volume"
else
    echo "[entrypoint] No cached custom nodes on volume (first boot)"
fi

# 1b. Symlink cached models from volume (instant)
if [ -d "$VOLUME_MODELS" ]; then
    for vol_dir in "$VOLUME_MODELS"/*/; do
        [ -d "$vol_dir" ] || continue
        dirname=$(basename "$vol_dir")
        target_dir="$COMFYUI_MODELS/$dirname"
        mkdir -p "$target_dir"
        for item in "$vol_dir"*; do
            [ -e "$item" ] || continue
            ln -sf "$item" "$target_dir/$(basename "$item")"
        done
    done
    echo "[entrypoint] Model symlinks created"
fi

# 1c. Patch handler.py for video output (VHS_VideoCombine uses 'gifs' key)
if ! grep -q 'gifs' /handler.py 2>/dev/null; then
    echo "[entrypoint] Patching handler.py for video output..."
    python3 -c '
import re
handler_path = "/handler.py"
with open(handler_path) as f:
    content = f.read()
pattern = r"(\n)([ \t]*)(if \"images\" in node_output:)"
match = re.search(pattern, content)
if match:
    indent = match.group(2)
    patch = "\n" + indent + "# [SCAIL patch] Map VHS_VideoCombine gifs output to images for video support\n" + indent + "if \"gifs\" in node_output and \"images\" not in node_output:\n" + indent + "    node_output[\"images\"] = node_output[\"gifs\"]\n" + indent + match.group(3)
    content = content[:match.start()] + patch + content[match.end():]
    with open(handler_path, "w") as f:
        f.write(content)
    print("[entrypoint] Patched handler.py: added gifs->images mapping")
else:
    print("[entrypoint] WARNING: Could not find patch target in handler.py")
' 2>&1 || true
fi

echo "[entrypoint] Phase 1 complete ($(date -u))."

# ============================================================
# PHASE 2: Start worker in BACKGROUND
# Worker becomes 'ready' immediately while we continue setup
# ============================================================
echo "[entrypoint] Starting /start.sh in background..."
/start.sh &
WORKER_PID=$!
echo "[entrypoint] Worker PID: $WORKER_PID"

# Wait for ComfyUI to fully start and import all libraries
# CRITICAL: pip install while ComfyUI imports causes ImportError race conditions
echo "[entrypoint] Waiting 45s for ComfyUI to finish importing..."
sleep 45
echo "[entrypoint] ComfyUI should be loaded. Starting heavy setup..."

# ============================================================
# PHASE 3: Heavy setup in FOREGROUND (keeps container alive)
# Install custom nodes, upgrade packages, download models
# ============================================================

# 3a. Install aria2 if not present
if ! command -v aria2c &>/dev/null; then
    echo "[setup] Installing aria2..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends aria2 && rm -rf /var/lib/apt/lists/*
fi

# 3b. ComfyUI version check
if [ -d "$COMFYUI_DIR/.git" ]; then
    CUR_VER=$(cd "$COMFYUI_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "[setup] ComfyUI version: $CUR_VER (using base image version)"
fi

# 3c. Integrity check on qwen safetensors file
QWEN_FILE="$VOLUME_MODELS/text_encoders/qwen_3_8b_fp8mixed.safetensors"
if [ -f "$QWEN_FILE" ]; then
    echo "[setup] Checking qwen file integrity..."
    python3 -c '
import struct, json, os, sys
fpath = "/runpod-volume/models/text_encoders/qwen_3_8b_fp8mixed.safetensors"
if not os.path.exists(fpath):
    sys.exit(0)
EXPECTED_SIZE = 8664848742
actual_size = os.path.getsize(fpath)
print(f"[setup] qwen size: {actual_size} (expected: {EXPECTED_SIZE})")
if actual_size < EXPECTED_SIZE:
    print("[setup] File truncated! Deleting...")
    os.remove(fpath)
    sys.exit(0)
try:
    with open(fpath, "rb") as f:
        header_size = struct.unpack("<Q", f.read(8))[0]
        header_data = f.read(header_size)
        header = json.loads(header_data.decode("utf-8"))
        data_start = 8 + header_size
        quant_keys = [k for k in header if "comfy_quant" in k]
        print(f"[setup] comfy_quant tensors: {len(quant_keys)}")
        if quant_keys:
            key = quant_keys[0]
            info = header[key]
            start, end = info["data_offsets"]
            size = end - start
            f.seek(data_start + start)
            tensor_bytes = f.read(size)
            if tensor_bytes == b"\x00" * size:
                print("[setup] comfy_quant ALL ZEROS - corrupt! Deleting...")
                os.remove(fpath)
            else:
                try:
                    json.loads(tensor_bytes)
                    print("[setup] comfy_quant data OK")
                except:
                    print("[setup] comfy_quant not valid JSON! Deleting...")
                    os.remove(fpath)
except Exception as e:
    print(f"[setup] Integrity check error: {e}")
' 2>&1 || true
fi

# 3d. Install custom nodes to volume (cached for future restarts)
mkdir -p "$VOLUME_NODES"
NEW_NODES_INSTALLED=0

install_node() {
    local repo_url="$1"
    local dirname="$2"
    local vol_node="$VOLUME_NODES/$dirname"
    local comfy_node="$COMFYUI_NODES/$dirname"

    if [ -d "$vol_node" ] && [ -f "$vol_node/__init__.py" -o -d "$vol_node/js" -o -f "$vol_node/nodes.py" ]; then
        ln -sf "$vol_node" "$comfy_node"
        echo "[setup] Node OK (cached): $dirname"
        return 0
    fi

    echo "[setup] Installing node: $dirname ..."
    rm -rf "$vol_node"
    git clone --depth 1 "$repo_url" "$vol_node" 2>&1 | tail -3
    if [ -f "$vol_node/requirements.txt" ]; then
        pip install -r "$vol_node/requirements.txt" --no-cache-dir 2>&1 | tail -3
    fi
    if [ -f "$vol_node/install.py" ]; then
        cd "$vol_node" && python install.py 2>&1 | tail -3 || true
    fi
    ln -sf "$vol_node" "$comfy_node"
    NEW_NODES_INSTALLED=$((NEW_NODES_INSTALLED + 1))
    echo "[setup] Installed: $dirname"
}

echo "[setup] Installing custom nodes..."
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
    echo "[setup] Installing onnxruntime-gpu..."
    pip install onnxruntime-gpu --no-cache-dir 2>&1 | tail -3 || pip install onnxruntime --no-cache-dir 2>&1 | tail -3
}

echo "[setup] Custom nodes done. New installs: $NEW_NODES_INSTALLED"

# 3e. Upgrade critical packages (AFTER custom nodes installed, ComfyUI is long past startup)
echo "[setup] Upgrading critical packages for flux2/qwen3 support..."
pip install --upgrade safetensors transformers tokenizers 2>&1 | tail -5 || true

# 3f. Download models
DOWNLOAD_ERRORS=0

validate_safetensors() {
    local filepath="$1"
    python3 -c '
import struct, sys
try:
    with open(sys.argv[1], "rb") as f:
        raw = f.read(8)
        if len(raw) < 8: sys.exit(1)
        hs = struct.unpack("<Q", raw)[0]
        if hs < 2 or hs > 200000000: sys.exit(1)
        f.read(min(hs, 4096)).decode("utf-8")
    sys.exit(0)
except: sys.exit(1)
' "$filepath" 2>/dev/null
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
                echo "[setup] CORRUPT: $filename -- re-downloading"
                rm -f "$dest_path"
            else
                echo "[setup] OK: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
                return 0
            fi
        else
            echo "[setup] OK: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
            return 0
        fi
    fi

    rm -f "$dest_path"
    echo "[setup] Downloading $filename ..."
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
        echo "[setup] Downloaded: $filename ($(stat -c%s "$dest_path" | numfmt --to=iec))"
    else
        echo "[setup] WARNING: $filename download failed!"
        rm -f "$dest_path"
        DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))
    fi
}

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
    echo "[setup] Trying ModelScope for Flux Klein..."
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
    echo "[setup] WARNING: $DOWNLOAD_ERRORS download(s) failed."
fi

# 3g. Sync symlinks again (for newly downloaded models)
if [ -d "$VOLUME_MODELS" ]; then
    echo "[setup] Refreshing model symlinks..."
    for vol_dir in "$VOLUME_MODELS"/*/; do
        [ -d "$vol_dir" ] || continue
        dirname=$(basename "$vol_dir")
        target_dir="$COMFYUI_MODELS/$dirname"
        mkdir -p "$target_dir"
        for item in "$vol_dir"*; do
            [ -e "$item" ] || continue
            ln -sf "$item" "$target_dir/$(basename "$item")"
        done
    done
fi

# 3h. Restart ComfyUI to pick up newly installed nodes + upgraded packages
echo "[setup] Restarting ComfyUI to load custom nodes and updated packages..."
sleep 3
pkill -f "python.*main.py.*--listen" 2>/dev/null || true
echo "[setup] ComfyUI restart signal sent. It will auto-restart via /start.sh supervisor."

echo "[setup] === Setup complete at $(date -u) ==="
echo "[setup] Worker is running (PID $WORKER_PID). Ready to process jobs."

# ============================================================
# PHASE 4: Wait for worker process (keeps container alive)
# ============================================================
wait $WORKER_PID
