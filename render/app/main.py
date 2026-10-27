import hmac
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import config
from .jobs import JobManager, QueueFull
from .source import SourceNotFound
from .storage import S3Storage
from .validation import cli_args, validate_folder, validate_name

log = logging.getLogger("render")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.S3_BUCKET:  # skip in tests / local runs without S3
        app.state.jobs = JobManager(S3Storage())
        app.state.jobs.start()
    yield
    if app.state.jobs is not None:
        app.state.jobs.stop()


app = FastAPI(title="HyperFrames render", lifespan=lifespan)
app.state.jobs = None


def get_jobs() -> JobManager:
    if app.state.jobs is None:
        raise HTTPException(503, "render not configured")
    return app.state.jobs


def check_key(x_api_key: str = Header(default="")) -> None:
    if config.API_KEY and not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(401, "invalid API key")


class RenderRequest(BaseModel):
    folder: str = Field(min_length=1)
    name: str = "out"
    quality: Literal["draft", "looks", "delivery"] = "looks"
    fps: Literal[24, 30, 60] = 30
    format: Literal["mp4"] = "mp4"
    workers: str | int = "auto"


@app.get("/health")
def health():
    return {"status": "ok"}


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
