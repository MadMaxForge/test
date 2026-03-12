#!/bin/bash
# start.sh — Run before ComfyUI starts to set up custom nodes from Network Volume
# This script checks for custom_nodes on the Network Volume and creates symlinks
# to the ComfyUI custom_nodes directory. It also installs Python dependencies.

VOLUME_ROOT="/runpod-volume"
COMFYUI_ROOT="/comfyui"

# Check for custom_nodes on volume
if [ -d "$VOLUME_ROOT/ComfyUI/custom_nodes" ]; then
    echo "[start.sh] Found custom_nodes on Network Volume"
    for node_dir in "$VOLUME_ROOT/ComfyUI/custom_nodes"/*/; do
        [ -d "$node_dir" ] || continue
        node_name=$(basename "$node_dir")
        target="$COMFYUI_ROOT/custom_nodes/$node_name"

        if [ ! -e "$target" ]; then
            ln -sf "$node_dir" "$target"
            echo "[start.sh]   Linked: $node_name"

            # Install Python dependencies if requirements.txt exists
            if [ -f "$node_dir/requirements.txt" ]; then
                echo "[start.sh]   Installing deps for $node_name..."
                pip install -r "$node_dir/requirements.txt" --no-cache-dir 2>/dev/null || true
            fi
        fi
    done
fi

# Check for venv on volume and add to PYTHONPATH
if [ -d "$VOLUME_ROOT/venv" ]; then
    echo "[start.sh] Found venv on Network Volume, adding to PYTHONPATH"
    SITE_PACKAGES=$(find "$VOLUME_ROOT/venv" -name "site-packages" -type d | head -1)
    if [ -n "$SITE_PACKAGES" ]; then
        export PYTHONPATH="$SITE_PACKAGES:${PYTHONPATH:-}"
        echo "[start.sh]   Added: $SITE_PACKAGES"
    fi
fi

echo "[start.sh] Setup complete, starting ComfyUI..."

# Call original start script
exec /start.sh
