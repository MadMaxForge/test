#!/bin/bash
# Custom entrypoint: symlink Network Volume models to ComfyUI model directories
# The Network Volume has models at /runpod-volume/runpod-slim/ComfyUI/models/
# But the official worker expects them at /comfyui/models/

VOLUME_MODELS="/runpod-volume/runpod-slim/ComfyUI/models"
COMFYUI_MODELS="/comfyui/models"

if [ -d "$VOLUME_MODELS" ]; then
    echo "[entrypoint] Network Volume found at $VOLUME_MODELS, creating symlinks..."
    
    # Iterate over each model subdirectory on the volume
    for vol_dir in "$VOLUME_MODELS"/*/; do
        [ -d "$vol_dir" ] || continue
        dirname=$(basename "$vol_dir")
        target_dir="$COMFYUI_MODELS/$dirname"
        
        # Create target directory if it doesn't exist
        mkdir -p "$target_dir"
        
        # Symlink each file and subdirectory from volume to comfyui models
        for item in "$vol_dir"*; do
            [ -e "$item" ] || continue
            itemname=$(basename "$item")
            # Skip placeholder files
            if [[ "$itemname" == put_* ]]; then
                continue
            fi
            # Create symlink (overwrite if exists)
            ln -sf "$item" "$target_dir/$itemname"
            echo "[entrypoint]   Linked: $dirname/$itemname"
        done
    done
    
    echo "[entrypoint] Model symlinks created successfully."
else
    echo "[entrypoint] No Network Volume models found at $VOLUME_MODELS"
fi

# Also symlink the input directory for uploaded files
VOLUME_INPUT="/runpod-volume/runpod-slim/ComfyUI/input"
COMFYUI_INPUT="/comfyui/input"
if [ -d "$VOLUME_INPUT" ]; then
    echo "[entrypoint] Symlinking input directory..."
    # Don't replace the whole dir, just ensure volume files are accessible
fi

# Run the original start script
echo "[entrypoint] Starting ComfyUI worker..."
exec /start.sh
