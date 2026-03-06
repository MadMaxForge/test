# Custom worker-comfyui that loads custom_nodes from Network Volume
# Based on official RunPod worker-comfyui with added symlink logic
FROM runpod/worker-comfyui:5.5.1-base

# Copy our custom start script that symlinks custom_nodes from Network Volume
COPY start.sh /custom-start.sh
RUN chmod +x /custom-start.sh

# Override the default CMD to use our custom start script
CMD ["/custom-start.sh"]
