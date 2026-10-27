# OmniVoice TTS API (POST /tts -> public S3 URL). Targets a CUDA GPU (e.g. AWS g4dn.xlarge, Vast.ai, RunPod).
# Build (from this folder):  docker buildx build --platform linux/amd64 -t omnivoice-tts .
# Run:                       docker run --gpus all -p 8000:8000 --env-file ../.env omnivoice-tts
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends software-properties-common curl \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get install -y --no-install-recommends python3.12 python3.12-venv git ffmpeg libsndfile1 tini \
  && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /opt/venv && useradd -m -u 1000 app
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
RUN pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the model (~3 GB) into the image: no internet / cold download at start.
RUN mkdir -p /opt/hf-cache && chown app:app /opt/hf-cache
USER app
ENV HOME=/home/app HF_HOME=/opt/hf-cache
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('k2-fsa/OmniVoice')"
ENV HF_HUB_OFFLINE=1

COPY app ./app
COPY voices ./voices

ENV VOICES_DIR=/app/voices DEVICE=cuda
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD curl -fs http://localhost:8000/health || exit 1
# One worker: the GPU model lives in-process and inference is serialized.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
