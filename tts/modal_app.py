"""OmniVoice TTS on Modal: GPU, scales to zero, billed per second while a container is up.

Serves the same FastAPI app as the Docker image (POST /tts, GET /voices, GET /health).

    pip install modal && modal setup
    modal secret create omnivoice-tts S3_ENDPOINT_URL=... S3_BUCKET=... AWS_REGION=... \
        AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... S3_PUBLIC_BASE_URL=...   # add API_KEY=... to require a key
    modal deploy tts/modal_app.py

Knobs (environment variables read at deploy time): MODAL_GPU, MODAL_SECRET_NAME, MODAL_SCALEDOWN_S, MODAL_MAX_CONTAINERS.
"""
import os
from pathlib import Path

import modal

HERE = Path(__file__).parent

APP_NAME = os.environ.get("MODAL_APP_NAME", "omnivoice-tts")
_GPUS = [g.strip() for g in os.environ.get("MODAL_GPU", "L4,T4,A10").split(",") if g.strip()]
GPU = _GPUS[0] if len(_GPUS) == 1 else _GPUS  # comma-separated = fallback order: Modal takes the first type that has capacity
SECRET_NAME = os.environ.get("MODAL_SECRET_NAME", "omnivoice-tts")
SCALEDOWN_S = int(os.environ.get("MODAL_SCALEDOWN_S", "120"))  # idle seconds before the container is shut down
MAX_CONTAINERS = int(os.environ.get("MODAL_MAX_CONTAINERS", "1"))  # hard cap on parallel GPUs (= cost cap)
MODEL_ID = "k2-fsa/OmniVoice"


def download_model() -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_ID)


image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install("torch==2.8.0", "torchaudio==2.8.0", index_url="https://download.pytorch.org/whl/cu126")
    .pip_install_from_requirements(str(HERE / "requirements.txt"))
    .env({"HF_HOME": "/models", "VOICES_DIR": "/app/voices", "DEVICE": "cuda", "PYTHONPATH": "/app"})
    .run_function(download_model)  # bake the model into the image so a cold start does not download it
    .env({"HF_HUB_OFFLINE": "1"})
    .add_local_dir(str(HERE / "app"), "/app/app")
    .add_local_dir(str(HERE / "voices"), "/app/voices")
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    gpu=GPU,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    scaledown_window=SCALEDOWN_S,
    max_containers=MAX_CONTAINERS,
    timeout=900,
)
@modal.concurrent(max_inputs=8)  # one model per container; Engine serializes inference, this only keeps /health and /voices responsive
@modal.asgi_app()
def web():
    from app.main import app as fastapi_app

    return fastapi_app
