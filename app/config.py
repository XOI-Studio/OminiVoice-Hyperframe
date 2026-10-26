import os
from pathlib import Path

VOICES_DIR = Path(os.environ.get("VOICES_DIR", "/app/voices"))
MODEL_ID = os.environ.get("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
DEVICE = os.environ.get("DEVICE", "cuda")  # cuda | cpu
DEFAULT_VOICE = os.environ.get("DEFAULT_VOICE", "narrator_male")
DEFAULT_STEPS = int(os.environ.get("DEFAULT_STEPS", "32"))
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", "8000"))
CHUNK_CHARS = int(os.environ.get("CHUNK_CHARS", "300"))
TAIL_SECONDS = float(os.environ.get("TAIL_SECONDS", "0.35"))

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"))
S3_PREFIX = os.environ.get("S3_PREFIX", "tts").strip("/")
# Optional CDN / custom domain in front of the bucket, e.g. https://cdn.example.com
S3_PUBLIC_BASE_URL = os.environ.get("S3_PUBLIC_BASE_URL", "").rstrip("/")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or None  # MinIO / LocalStack

API_KEY = os.environ.get("API_KEY", "")  # empty = no auth
