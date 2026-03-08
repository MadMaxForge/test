# Custom RunPod ComfyUI worker with WAN 2.1 InfiniteTalk custom nodes
# Based on official RunPod worker-comfyui (latest stable)
# Build trigger: endpoint exz6k77qinnio4
FROM runpod/worker-comfyui:5.7.1-base

# Install aria2 for fast multi-threaded model downloads with resume support
RUN apt-get update && apt-get install -y --no-install-recommends aria2 && \
    rm -rf /var/lib/apt/lists/*

# Install custom nodes required for WAN 2.1 InfiniteTalk workflow
WORKDIR /comfyui/custom_nodes

# 1. ComfyUI-WanVideoWrapper (kijai) - MultiTalkModelLoader, WanVideoSampler,
#    WanVideoDecode, WanVideoModelLoader, etc.
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

# 4. audio-separation-nodes-comfyui (christian-byrne) - AudioSeparation
RUN git clone https://github.com/christian-byrne/audio-separation-nodes-comfyui.git && \
    cd audio-separation-nodes-comfyui && \
    pip install -r requirements.txt --no-cache-dir

# 5. ComfyUI-AudioScheduler (a1lazydog) - AudioToAudioData, AudioToFFTs
RUN git clone https://github.com/a1lazydog/ComfyUI-AudioScheduler.git && \
    cd ComfyUI-AudioScheduler && \
    ([ -f requirements.txt ] && pip install -r requirements.txt --no-cache-dir || true)

# Go back to root
WORKDIR /

# Patch handler.py: add video output support (VHS_VideoCombine uses 'gifs' key)
# The default handler only looks for 'images' in ComfyUI output, but video nodes
# like VHS_VideoCombine return results under 'gifs'. This patch makes the handler
# also collect video files so they are returned as base64 in the API response.
COPY patch_handler.py /patch_handler.py
RUN python3 /patch_handler.py && rm /patch_handler.py

# Custom entrypoint: symlink Network Volume models before starting ComfyUI
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
