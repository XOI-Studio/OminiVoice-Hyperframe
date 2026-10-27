# OmniVoice TTS API (POST /tts -> public S3 URL) + HyperFrames render API (POST /render -> job -> public mp4 URL).
# Targets AWS g4dn.xlarge (NVIDIA T4, cheapest CUDA box). Render runs on CPU (software GL Chrome).
# Build:  docker buildx build --platform linux/amd64 -t omnivoice-tts .
# Run:    docker run --gpus all --shm-size=1g -p 8000:8000 -e S3_BUCKET=... -e AWS_REGION=ap-southeast-2 omnivoice-tts
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends software-properties-common curl \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get install -y --no-install-recommends python3.12 python3.12-venv git ffmpeg libsndfile1 \
  && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /opt/venv && useradd -m -u 1000 app
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
RUN pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126
COPY requirements.txt .
RUN pip install -r requirements.txt

# Render toolchain: Node 22 + pinned HyperFrames CLI + the shared libs Chrome needs (+ Vietnamese-capable fonts).
ARG HYPERFRAMES_VERSION=0.8.58
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libxshmfence1 \
    fonts-noto-core fonts-noto-color-emoji fonts-liberation \
  && rm -rf /var/lib/apt/lists/* \
  && npm install -g hyperframes@${HYPERFRAMES_VERSION} \
  && npm cache clean --force
ENV HYPERFRAMES_NO_TELEMETRY=1 HYPERFRAMES_BIN=hyperframes

# From here on run as the non-root user: it owns the model cache and the baked Chrome.
RUN mkdir -p /opt/hf-cache && chown app:app /opt/hf-cache
USER app
ENV HOME=/home/app

# Bake the model (~3 GB) and the pinned Chrome into the image: no internet / cold download at start.
ENV HF_HOME=/opt/hf-cache
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('k2-fsa/OmniVoice')"
ENV HF_HUB_OFFLINE=1
# The CLI's own `browser ensure` downloader hangs after the download in some environments, so fetch the same pinned
# Chrome headless shell directly (bounded, retried) and point HyperFrames at it.
ARG CHROME_VERSION=152.0.7977.30
ENV HYPERFRAMES_BROWSER_PATH=/home/app/chrome/chrome-headless-shell-linux64/chrome-headless-shell
RUN curl -fsSL --retry 5 --connect-timeout 15 --max-time 300 -o /tmp/chrome.zip \
    https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-headless-shell-linux64.zip \
  && python -m zipfile -e /tmp/chrome.zip /home/app/chrome \
  && chmod +x "$HYPERFRAMES_BROWSER_PATH" && rm /tmp/chrome.zip \
  && "$HYPERFRAMES_BROWSER_PATH" --version

# Reap zombies from Chrome/ffmpeg children (uvicorn is PID 1 otherwise).
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
USER app

COPY app ./app
COPY voices ./voices

ENV VOICES_DIR=/app/voices DEVICE=cuda
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD curl -fs http://localhost:8000/health || exit 1
ENTRYPOINT ["/usr/bin/tini","--"]
# One worker: the GPU model lives in-process and inference is serialized.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
