# Custom RunPod ComfyUI worker for WAN SCAIL + Flux Klein motion control workflow
# Based on official RunPod worker-comfyui (latest stable)
# Endpoint: 07omy70ke8uteh | Volume: wimq502ije | Base image deployment (runtime node install)
FROM runpod/worker-comfyui:5.7.1-base

# Install aria2 for fast multi-threaded model downloads with resume support
RUN apt-get update && apt-get install -y --no-install-recommends aria2 && \
    rm -rf /var/lib/apt/lists/*

# Install custom nodes required for WAN SCAIL + Flux Klein workflow
WORKDIR /comfyui/custom_nodes

# 1. ComfyUI-WanVideoWrapper (kijai) - WanVideoModelLoader, WanVideoSamplerv2,
#    WanVideoDecode, WanVideoAddSCAILPoseEmbeds, WanVideoAddSCAILReferenceEmbeds, etc.
RUN git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    cd ComfyUI-WanVideoWrapper && \
    pip install -r requirements.txt --no-cache-dir

# 2. ComfyUI-SCAIL-Pose (kijai) - PoseDetectionVitPoseToDWPose, RenderNLFPoses,
#    DownloadAndLoadNLFModel, NLFPredict, OnnxDetectionModelLoader
RUN git clone https://github.com/kijai/ComfyUI-SCAIL-Pose.git && \
    cd ComfyUI-SCAIL-Pose && \
    pip install -r requirements.txt --no-cache-dir

# 3. ComfyUI-VideoHelperSuite (Kosinkadink) - VHS_LoadVideo, VHS_VideoCombine, VHS_VideoInfo
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    cd ComfyUI-VideoHelperSuite && \
    pip install -r requirements.txt --no-cache-dir

# 4. ComfyUI-KJNodes (kijai) - ImageResizeKJv2, PathchSageAttentionKJ, Frame Select
RUN git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    cd ComfyUI-KJNodes && \
    pip install -r requirements.txt --no-cache-dir

# 5. ComfyUI-Impact-Pack (ltdrdata) - ImpactSwitch
RUN git clone https://github.com/ltdrdata/ComfyUI-Impact-Pack.git && \
    cd ComfyUI-Impact-Pack && \
    ([ -f requirements.txt ] && pip install -r requirements.txt --no-cache-dir || true) && \
    python install.py || true

# 6. comfyui-easy-use - easy mathInt
RUN git clone https://github.com/yolain/ComfyUI-Easy-Use.git && \
    cd ComfyUI-Easy-Use && \
    ([ -f requirements.txt ] && pip install -r requirements.txt --no-cache-dir || true)

# 7. ComfyUI-WanAnimatePreprocess - ImageStitch
RUN git clone https://github.com/kijai/ComfyUI-WanAnimatePreprocess.git && \
    cd ComfyUI-WanAnimatePreprocess && \
    ([ -f requirements.txt ] && pip install -r requirements.txt --no-cache-dir || true)

# 8. comfyui_controlnet_aux - DWPreprocessor
RUN git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git && \
    cd comfyui_controlnet_aux && \
    pip install -r requirements.txt --no-cache-dir

# 9. RES4LYF - ReferenceLatent, EmptyFlux2LatentImage
RUN git clone https://github.com/ClownsharkBatwing/RES4LYF.git && \
    cd RES4LYF && \
    ([ -f requirements.txt ] && pip install -r requirements.txt --no-cache-dir || true)

# Install onnxruntime-gpu for VitPose detection models
RUN pip install onnxruntime-gpu --no-cache-dir || pip install onnxruntime --no-cache-dir

# Go back to root
WORKDIR /

# Patch handler.py: add video output support (VHS_VideoCombine uses 'gifs' key)
RUN python3 -c "import re; f='/handler.py'; c=open(f).read(); c,n=re.subn(r'(\n)((\s*)if \"images\" in node_output:)',r'\1\3if \"gifs\" in node_output and \"images\" not in node_output:\n\3    node_output[\"images\"] = node_output[\"gifs\"]\n\2',c,count=1); open(f,'w').write(c); print(f'Patched handler.py: {n} replacement(s)')"

# Custom entrypoint and start script
COPY worker/entrypoint.sh /entrypoint.sh
COPY worker/start.sh /start-custom.sh
RUN chmod +x /entrypoint.sh /start-custom.sh

CMD ["/entrypoint.sh"]
