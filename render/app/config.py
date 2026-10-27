import os

HYPERFRAMES_BIN = os.environ.get("HYPERFRAMES_BIN", "hyperframes")

S3_BUCKET = os.environ.get("S3_BUCKET", "")  # where mp4 files and job state are written
SOURCE_BUCKET = os.environ.get("SOURCE_BUCKET") or S3_BUCKET  # where the project folders / zips are read from
S3_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"))
# Optional CDN / custom domain in front of the bucket, e.g. https://cdn.example.com
S3_PUBLIC_BASE_URL = os.environ.get("S3_PUBLIC_BASE_URL", "").rstrip("/")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or None  # R2 / MinIO / LocalStack

API_KEY = os.environ.get("API_KEY", "")  # empty = open API, no key needed

RENDER_PREFIX = os.environ.get("RENDER_PREFIX", "renders").strip("/")
RENDER_TIMEOUT_S = int(os.environ.get("RENDER_TIMEOUT_S", "7200"))
RENDER_MAX_DOWNLOAD_MB = int(os.environ.get("RENDER_MAX_DOWNLOAD_MB", "4096"))
RENDER_MAX_FILES = int(os.environ.get("RENDER_MAX_FILES", "5000"))
RENDER_QUEUE_MAX = int(os.environ.get("RENDER_QUEUE_MAX", "10"))
