#!/usr/bin/env bash
# Custom start script for worker-comfyui that loads custom_nodes from Network Volume
# The official worker only loads models from /runpod-volume/models/
# This script additionally symlinks custom_nodes from the Network Volume

echo "worker-comfyui-custom: Checking for custom_nodes on Network Volume..."

VOLUME_CUSTOM_NODES="/runpod-volume/ComfyUI/custom_nodes"
COMFYUI_CUSTOM_NODES="/comfyui/custom_nodes"

if [ -d "$VOLUME_CUSTOM_NODES" ]; then
    echo "worker-comfyui-custom: Found custom_nodes on Network Volume, creating symlinks..."
    for node_dir in "$VOLUME_CUSTOM_NODES"/*/; do
        node_name=$(basename "$node_dir")
        target="$COMFYUI_CUSTOM_NODES/$node_name"
        if [ ! -e "$target" ] && [ "$node_name" != "__pycache__" ]; then
            ln -sf "$node_dir" "$target"
            echo "worker-comfyui-custom: Linked $node_name"
        fi
    done

    # Install Python dependencies from custom nodes' requirements.txt
    for req_file in "$VOLUME_CUSTOM_NODES"/*/requirements.txt; do
        if [ -f "$req_file" ]; then
            node_name=$(basename "$(dirname "$req_file")")
            echo "worker-comfyui-custom: Installing requirements for $node_name..."
            pip install -q -r "$req_file" 2>/dev/null || true
        fi
    done

    echo "worker-comfyui-custom: Custom nodes linked successfully"
else
    echo "worker-comfyui-custom: No custom_nodes found on Network Volume at $VOLUME_CUSTOM_NODES"
fi

# Also check for a venv on the volume and install any additional packages
VOLUME_VENV="/runpod-volume/ComfyUI/.venv-cu128"
if [ -d "$VOLUME_VENV/lib" ]; then
    echo "worker-comfyui-custom: Found venv on Network Volume, adding site-packages to path..."
    VENV_SITE=$(find "$VOLUME_VENV/lib" -maxdepth 2 -name "site-packages" -type d | head -1)
    if [ -n "$VENV_SITE" ]; then
        export PYTHONPATH="${VENV_SITE}:${PYTHONPATH}"
        echo "worker-comfyui-custom: Added $VENV_SITE to PYTHONPATH"
    fi
fi

# Run the original start script
echo "worker-comfyui-custom: Starting original worker..."
exec /start.sh
