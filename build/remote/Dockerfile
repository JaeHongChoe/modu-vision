# Isolated remote worker runtime. Application code is uploaded per run, not baked in.
# Pin the default PyTorch CUDA base digest for reproducible worker builds.
# Override BASE_IMAGE when a compatible host needs a different base.
ARG BASE_IMAGE=pytorch/pytorch@sha256:faa67ebc9c9733bf35b7dae3f8640f5b4560fd7f2e43c72984658d63625e4487
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TORCH_HOME=/opt/modu-vision/torch-cache \
    MODU_VISION_WEIGHTS_MANIFEST=/opt/modu-vision/torch-cache/weights-manifest.json \
    MODU_VISION_MODEL_DEPENDENCIES_LOCK=/opt/modu-vision/requirements-models.lock

# Match the base image's torch 2.6.0 and CUDA 12.6 build. --no-deps keeps the
# preinstalled CUDA torch binary instead of replacing it during resolution.
RUN python -m pip install --no-cache-dir --no-deps \
    --index-url https://download.pytorch.org/whl/cu126 \
    torchvision==0.21.0+cu126

# Train imports the engine; evaluation imports its FastAPI/Pydantic route module.
# ONNX and ONNX Runtime are needed for the Stage 6 ONNX export and smoke test.
RUN python -m pip install --no-cache-dir \
    "numpy>=1.26,<3" \
    "Pillow>=10.4,<12" \
    "opencv-python-headless>=4.10,<5" \
    "scikit-learn>=1.5,<2" \
    "psutil>=6,<8" \
    "fastapi>=0.115,<1" \
    "pydantic>=2.8,<3" \
    "python-multipart>=0.0.12" \
    "onnx>=1.17,<2" \
    "onnxruntime>=1.19,<2"

COPY requirements-models.lock /opt/modu-vision/requirements-models.lock
# Private build contexts may upload this file at 0600. The numeric worker UID
# must read the installed environment contract during runtime preflight.
RUN chmod 644 /opt/modu-vision/requirements-models.lock \
    && python -m pip install --no-cache-dir -r /opt/modu-vision/requirements-models.lock
# The detector distribution pulls GUI OpenCV. Headless workers need one cv2
# provider; reinstall after removing GUI wheels because they share cv2 files.
RUN python -m pip uninstall -y opencv-python opencv-contrib-python \
    && python -m pip install --no-cache-dir --no-deps --force-reinstall opencv-python-headless==4.11.0.86
RUN python -c "import torch, torchvision, cv2, numpy, PIL, sklearn, psutil, fastapi, pydantic, onnx, onnxruntime, timm, safetensors, huggingface_hub, ultralytics; assert torch.__version__.startswith('2.6.0'); assert timm.is_model('vit_small_patch16_dinov3.lvd1689m'); assert ultralytics.__version__ == '8.4.41'"

# The worker has --network none at run time. Cache every DEFAULT checkpoint
# reachable from the current training model factories, then record full hashes.
COPY cache_weights.py /opt/modu-vision/cache_weights.py
RUN python /opt/modu-vision/cache_weights.py \
    && chmod -R a+rX /opt/modu-vision/torch-cache
