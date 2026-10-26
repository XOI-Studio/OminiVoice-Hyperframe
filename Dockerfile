# OmniVoice TTS API (POST /tts -> public S3 URL). Targets AWS g4dn.xlarge (NVIDIA T4, cheapest CUDA box).
# Build:  docker build -t omnivoice-tts .
# Run:    docker run --gpus all -p 8000:8000 -e S3_BUCKET=... -e AWS_REGION=ap-southeast-1 omnivoice-tts
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends software-properties-common curl \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get install -y --no-install-recommends python3.12 python3.12-venv git ffmpeg libsndfile1 \
  && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
RUN pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu124
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the model (~3 GB) into the image: no internet / cold download at start.
ENV HF_HOME=/opt/hf-cache
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('k2-fsa/OmniVoice')"
ENV HF_HUB_OFFLINE=1

COPY app ./app
COPY voices ./voices

ENV VOICES_DIR=/app/voices DEVICE=cuda
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD curl -fs http://localhost:8000/health || exit 1
# One worker: the GPU model lives in-process and inference is serialized.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
