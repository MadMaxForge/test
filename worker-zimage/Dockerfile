# Z-Image Turbo serverless endpoint for RunPod
# Single-pass photo generation with LoRA chain
# Build trigger: endpoint z-image-turbo
FROM runpod/worker-comfyui:5.7.1-base

# gdown for downloading LoRA files from Google Drive
RUN pip install --no-cache-dir gdown

# The base image extra_model_paths.yaml only maps unet/clip/vae/loras/checkpoints.
# UNETLoader uses 'diffusion_models' folder_name and CLIPLoader uses 'text_encoders',
# which are DIFFERENT from 'unet' and 'clip'. We must add both folder_names.
RUN printf '  diffusion_models: models/diffusion_models/\n  text_encoders: models/text_encoders/\n' >> /comfyui/extra_model_paths.yaml

# Verify the yaml is valid after append
RUN cat /comfyui/extra_model_paths.yaml && python -c "import yaml; yaml.safe_load(open('/comfyui/extra_model_paths.yaml'))"

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set NETWORK_VOLUME_DEBUG for troubleshooting model paths
ENV NETWORK_VOLUME_DEBUG=true

CMD ["/entrypoint.sh"]
