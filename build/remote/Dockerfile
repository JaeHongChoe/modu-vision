# Isolated remote worker runtime. Application code is uploaded per run, not baked in.
# Pin the default PyTorch CUDA base digest for reproducible worker builds.
# Override BASE_IMAGE when a compatible host needs a different base.
ARG BASE_IMAGE=pytorch/pytorch@sha256:faa67ebc9c9733bf35b7dae3f8640f5b4560fd7f2e43c72984658d63625e4487
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TORCH_HOME=/opt/modu-vision/torch-cache \
    MODU_VISION_WEIGHTS_MANIFEST=/opt/modu-vision/torch-cache/weights-manifest.json

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

RUN python -c "import torch, torchvision, cv2, numpy, PIL, sklearn, psutil, fastapi, pydantic, onnx, onnxruntime; assert torch.__version__.startswith('2.6.0')"

# The worker has --network none at run time. Cache every DEFAULT checkpoint
# reachable from the current training model factories, then record full hashes.
COPY cache_weights.py /opt/modu-vision/cache_weights.py
RUN python /opt/modu-vision/cache_weights.py \
    && chmod -R a+rX /opt/modu-vision/torch-cache
