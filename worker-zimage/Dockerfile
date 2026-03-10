# Z-Image Turbo serverless endpoint for RunPod
# Single-pass photo generation with LoRA chain
# Build trigger: endpoint z-image-turbo
FROM runpod/worker-comfyui:5.7.1-base

# gdown for downloading LoRA files from Google Drive
RUN pip install --no-cache-dir gdown

# Update extra_model_paths.yaml to include diffusion_models and text_encoders
# The base image only maps checkpoints/clip/loras/vae/unet
RUN printf '  diffusion_models: models/diffusion_models/\n  text_encoders: models/text_encoders/\n' >> /comfyui/extra_model_paths.yaml

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
