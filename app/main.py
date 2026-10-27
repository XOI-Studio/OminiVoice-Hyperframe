import hashlib
import hmac
import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import config
from .audio import encode
from .engine import Engine
from .render.jobs import JobManager, QueueFull
from .render.source import SourceNotFound
from .render.validation import cli_args, validate_folder, validate_name
from .storage import S3Storage

log = logging.getLogger("tts")
app = FastAPI(title="OmniVoice TTS")
app.state.engine = None
app.state.storage = None
app.state.jobs = None


def get_engine() -> Engine:
    if app.state.engine is None:
        app.state.engine = Engine()
    return app.state.engine


def get_storage() -> S3Storage:
    if app.state.storage is None:
        app.state.storage = S3Storage()
    return app.state.storage


def get_jobs() -> JobManager:
    if app.state.jobs is None:
        raise HTTPException(503, "render not configured")
    return app.state.jobs


def check_key(x_api_key: str = Header(default="")) -> None:
    if config.API_KEY and not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(401, "invalid API key")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = config.DEFAULT_VOICE
    language: str = "vi"
    format: Literal["wav", "mp3"] = "wav"
    steps: int = Field(config.DEFAULT_STEPS, ge=4, le=64, description="16 = draft, 32 = final")
    seed: int | None = Field(None, description="Reproducible output when set")
    wpm: float | None = Field(None, gt=0, le=400, description="Target words per minute (optional pacing)")


class TTSResponse(BaseModel):
    url: str
    voice: str
    format: str
    duration_s: float | None = None
    cached: bool


class RenderRequest(BaseModel):
    folder: str = Field(min_length=1)
    name: str = "out"
    quality: Literal["draft", "looks", "delivery"] = "looks"
    fps: Literal[24, 30, 60] = 30
    format: Literal["mp4"] = "mp4"
    workers: str | int = "auto"


@app.on_event("startup")
def warm() -> None:
    if config.S3_BUCKET:  # skip in tests / local runs without S3
        get_engine().load()
        app.state.jobs = JobManager(get_storage())
        app.state.jobs.start()


@app.on_event("shutdown")
def shutdown() -> None:
    if app.state.jobs is not None:
        app.state.jobs.stop()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/voices", dependencies=[Depends(check_key)])
def voices(category: str | None = None, channel: str | None = None, engine: Engine = Depends(get_engine)):
    return {
        name: meta for name, meta in engine.voices().items()
        if (not category or meta.get("category") == category) and (not channel or meta.get("channel") == channel)
    }


@app.post("/tts", response_model=TTSResponse, dependencies=[Depends(check_key)])
def tts(req: TTSRequest, engine: Engine = Depends(get_engine), storage: S3Storage = Depends(get_storage)):
    text = req.text.strip()
    if not text:
        raise HTTPException(422, "text is empty")
    if len(text) > config.MAX_TEXT_CHARS:
        raise HTTPException(413, f"text exceeds {config.MAX_TEXT_CHARS} chars")
    if req.voice not in engine.voices():
        raise HTTPException(404, f"unknown voice '{req.voice}'; GET /voices for the list")

    digest = hashlib.sha256(
        f"{text}|{req.voice}|{req.language}|{req.steps}|{req.seed}|{req.wpm}|{req.format}".encode()
    ).hexdigest()[:24]
    key = f"{config.S3_PREFIX}/{req.voice}/{digest}.{req.format}" if config.S3_PREFIX else f"{req.voice}/{digest}.{req.format}"

    if storage.exists(key):  # identical request already synthesized
        return TTSResponse(url=storage.url(key), voice=req.voice, format=req.format, cached=True)

    audio, sr = engine.synthesize(text, req.voice, req.language, req.steps, req.seed, req.wpm)
    data, content_type = encode(audio, sr, req.format)
    storage.put(key, data, content_type)
    return TTSResponse(url=storage.url(key), voice=req.voice, format=req.format, duration_s=round(len(audio) / sr, 3), cached=False)


@app.post("/render", status_code=202, dependencies=[Depends(check_key)])
def render(req: RenderRequest, jobs: JobManager = Depends(get_jobs)):
    try:
        folder = validate_folder(req.folder)
        name = validate_name(req.name)
        cli_args(req.quality, req.fps, req.workers, Path("out.mp4"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        job = jobs.submit(folder, name, req.quality, req.fps, req.workers)
    except SourceNotFound:
        raise HTTPException(404, "source not found")
    except QueueFull:
        raise HTTPException(429, "render queue full")
    except Exception:
        log.exception("failed to queue render job")
        raise HTTPException(503, "storage unavailable")
    return {"job_id": job["job_id"], "status": "queued", "status_url": f"/jobs/{job['job_id']}"}


@app.get("/jobs/{job_id}", dependencies=[Depends(check_key)])
def get_job(job_id: str, jobs: JobManager = Depends(get_jobs)):
    job = jobs.get(job_id) if re.fullmatch(r"[0-9a-f]{32}", job_id) else None
    if job is None:
        raise HTTPException(404, "job not found")
    return job
