FROM runpod/worker-comfyui:5.7.1-base

# Install aria2 and wget for model downloads
RUN apt-get update && apt-get install -y aria2 wget && rm -rf /var/lib/apt/lists/*

# Pre-download FLUX AE VAE into image (335 MB)
# This avoids runtime download issues with HF xet-bridge CDN
# aria2c parallel downloads corrupt this file; wget works reliably at build time
RUN mkdir -p /opt/models/vae && \
    wget -q --show-progress -O /opt/models/vae/ae.safetensors \
    "https://huggingface.co/Comfy-Org/z_image/resolve/main/split_files/vae/ae.safetensors" && \
    ls -la /opt/models/vae/ae.safetensors

# Install custom nodes
# RES4LYF - for ClownsharKSampler_Beta
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/ClownsharkBatwing/RES4LYF.git && \
    cd RES4LYF && pip install -r requirements.txt 2>/dev/null || true

# ComfyUI-KJNodes - included in workflow (SageAttention nodes disabled but node type still needed)
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    cd ComfyUI-KJNodes && pip install -r requirements.txt 2>/dev/null || true

# rgthree-comfy - for Image Comparer (optional but included in workflow)
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/rgthree/rgthree-comfy.git && \
    cd rgthree-comfy && pip install -r requirements.txt 2>/dev/null || true

# Update extra_model_paths.yaml to include diffusion_models and text_encoders
# The base image only maps checkpoints/clip/loras/vae/unet - we need these too
RUN printf '  diffusion_models: models/diffusion_models/\n  text_encoders: models/text_encoders/\n' >> /comfyui/extra_model_paths.yaml

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
