# Custom RunPod ComfyUI worker with WAN 2.1 InfiniteTalk custom nodes
# Based on official RunPod worker-comfyui (latest stable)
FROM runpod/worker-comfyui:5.7.1-base

# Install custom nodes required for WAN 2.1 InfiniteTalk workflow
WORKDIR /comfyui/custom_nodes

# 1. ComfyUI-WanVideoWrapper (kijai) - MultiTalkModelLoader, WanVideoSampler,
#    WanVideoDecode, WanVideoModelLoader, AudioSeparation, etc.
RUN git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    cd ComfyUI-WanVideoWrapper && \
    pip install -r requirements.txt --no-cache-dir

# 2. ComfyUI-VideoHelperSuite (Kosinkadink) - VHS_VideoCombine
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    cd ComfyUI-VideoHelperSuite && \
    pip install -r requirements.txt --no-cache-dir

# 3. ComfyUI-KJNodes (kijai) - ImageResizeKJv2, GetImageSizeAndCount, INTConstant
RUN git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    cd ComfyUI-KJNodes && \
    pip install -r requirements.txt --no-cache-dir

# Go back to root
WORKDIR /

# Use the default start script from the official worker
CMD ["/start.sh"]
