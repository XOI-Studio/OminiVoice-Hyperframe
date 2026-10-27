# HyperFrames render API (POST /render -> job -> public mp4 URL). CPU only: Chrome renders with software GL,
# so this image needs no GPU, PyTorch or model and runs on any cheap x86 box (Fargate, EC2, Cloud Run...).
# Build (from this folder):  docker buildx build --platform linux/amd64 -t hyperframes-render .
# Run:                       docker run --shm-size=1g -p 8000:8000 --env-file ../.env hyperframes-render
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
# Node 22 + ffmpeg + the shared libs Chrome needs (+ Vietnamese-capable fonts) + tini to reap Chrome/ffmpeg zombies.
RUN apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg tini \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libxshmfence1 \
    fonts-noto-core fonts-noto-color-emoji fonts-liberation \
  && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

# Pinned HyperFrames CLI.
ARG HYPERFRAMES_VERSION=0.8.58
RUN npm install -g hyperframes@${HYPERFRAMES_VERSION} && npm cache clean --force
ENV HYPERFRAMES_NO_TELEMETRY=1 HYPERFRAMES_BIN=hyperframes

RUN useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# The CLI's own `browser ensure` downloader hangs after the download in some environments, so fetch the same pinned
# Chrome headless shell directly (bounded, retried) and point HyperFrames at it.
USER app
ENV HOME=/home/app
ARG CHROME_VERSION=152.0.7977.30
ENV HYPERFRAMES_BROWSER_PATH=/home/app/chrome/chrome-headless-shell-linux64/chrome-headless-shell
RUN curl -fsSL --retry 5 --connect-timeout 15 --max-time 300 -o /tmp/chrome.zip \
    https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-headless-shell-linux64.zip \
  && python -m zipfile -e /tmp/chrome.zip /home/app/chrome \
  && chmod +x "$HYPERFRAMES_BROWSER_PATH" && rm /tmp/chrome.zip \
  && "$HYPERFRAMES_BROWSER_PATH" --version

COPY app ./app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs http://localhost:8000/health || exit 1
# One worker: the job manager (queue + render thread) lives in-process and renders one project at a time.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
