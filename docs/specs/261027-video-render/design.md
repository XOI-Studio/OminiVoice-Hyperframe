# Design: POST /render — render a HyperFrames project from S3 in the TTS image

**Decision:** Add an async render API to the existing FastAPI image. `POST /render {folder}` queues a job; a single in-process worker downloads the HyperFrames project from S3 (prefix or `.zip`), runs the pinned `hyperframes@0.8.58` CLI (`render`) in a subprocess, uploads the mp4 under `renders/…`, and records progress as JSON in S3. `GET /jobs/{id}` returns status and the public URL. Audio is always already in the folder; `POST /tts` stays as the separate voice generator.

## Context
- User wants one image that does TTS and render. Input is a "folder" on S3 holding a HyperFrames project (`index.html`, `compositions/`, `assets/` with images, audio, fonts), or a zip of it. Verified against the user's own project `XoiSoftware/vibe-motion` (`hyperframes.json`, `npx hyperframes render --quality looks --output …`).
- The channel engines in `/Youtube/{SuMuc,ChuyenMa}` (render-short.py, render-story-video.py) are **out of scope** — user clarified the image renders HyperFrames projects from many channels.
- HyperFrames needs Node ≥ 22, FFmpeg and Chrome (headless shell). Software GL on Linux; each worker ≈ 256 MB RAM. Target box g4dn.xlarge (4 vCPU, 16 GB); free-tier accounts have no GPU.
- Current image: `nvidia/cuda:12.4.1` + Python 3.12 venv, uvicorn single worker, boto3, S3 public URL helper (`app/storage.py`).
- Render takes minutes → cannot be a sync request. No DB in the project.

## Decisions
- Async API + status JSON on S3, one job at a time (in-process queue, max 10 queued) — no DB/SQS, matches current single-container deploy.
- Input `folder` = S3 key in `SOURCE_BUCKET` (default `S3_BUCKET`). Ends with `.zip` → unzip; otherwise prefix → download every object except under `renders/`. Project root = folder root, or its only top-level directory.
- Output goes to `renders/<folder-slug>/<name>-<job8>.mp4` in `S3_BUCKET` (public URL like TTS) — not beside the source, so one public prefix policy (`renders/*`) is enough and sources stay private.
- Render options are an allowlist: `quality` (draft|looks|delivery, default looks), `fps` (24|30|60, 30), `format` (mp4), `workers` (auto|1–8), `name`.
- Pin `hyperframes@0.8.58` (the user's version) globally in the image; Chrome baked in at build; run as non-root.
- Same `X-API-Key` auth as `/tts`. TTS and render may overlap (own locks); documented risk on 4 vCPU.
- Job state survives restarts only as "failed: interrupted" (non-terminal state in S3 but unknown to this process).

## Contracts
`POST /render` (X-API-Key) body `{folder, name="out", quality="looks", fps=30, format="mp4", workers="auto"}`
→ `202 {job_id, status:"queued", status_url:"/jobs/<id>"}`.
Errors: 401 bad key · 422 invalid folder (empty, `..`, leading `/`, chars outside `[A-Za-z0-9._/ -]`) or bad option · 404 folder/zip not found in S3 (checked before queueing) · 429 queue full.

`GET /jobs/{job_id}` → `{job_id, status, folder, name, url?, render_s?, error?, log_tail?, created_at, updated_at}`;
`status ∈ queued|downloading|rendering|uploading|done|failed`. 404 unknown id. State object: `jobs/<job_id>.json` in `S3_BUCKET`.

Env: `SOURCE_BUCKET`, `RENDER_PREFIX=renders`, `RENDER_TIMEOUT_S=7200`, `RENDER_MAX_DOWNLOAD_MB=4096`, `RENDER_QUEUE_MAX=10`.

## Data changes
None. Image: add Node 22 (NodeSource), `npm i -g hyperframes@0.8.58`, Chrome headless shell via the HyperFrames browser command, Chrome shared libs, fonts needed by compositions (Vietnamese-capable). Expect image +1.5–2.5 GB.

## Rejected options
- `hyperframes render --docker` — needs the Docker socket inside the container.
- `hyperframes lambda/cloudrun render` — remote infra, not "one image".
- SQS + worker container — durable and scalable but needs SQS, IAM and two run modes; revisit if volume grows.
- Run-once CLI/ECS task — no HTTP surface; user wants API.
- Reusing SuMuc/ChuyenMa engines — wrong scope per user.

## Risks & rollout
- Compositions run arbitrary HTML/JS in Chrome → only render folders from the user's own bucket; zip extraction guards against zip-slip, symlinks, >5000 files, > cap uncompressed size; subprocess has timeout and process-group kill.
- Software-GL render is slow/heavy on 4 vCPU → `workers` allowlist, one job at a time, timeout; mitigation later: bigger box or Lambda/Cloud Run render.
- Disk: project + frames; use per-job temp dir, always cleaned in `finally`.
- Rollback: image tag per commit in ECR; endpoints are additive — `/tts` untouched.
- Bucket policy must also allow public `GetObject` on `renders/*` (README update).

## Test strategy
- Unit (fake S3 + fake runner): folder validation, zip-slip/symlink/limits, prefix download skips `renders/`, project-root detection, option allowlist → CLI args, status transitions incl. failure/timeout, queue full, restart-interrupted reporting.
- API (TestClient): 202/401/404/422/429, `/jobs` shapes; `/tts` regression.
- Integration (real CLI): render a tiny 2 s HyperFrames project with `--quality draft` locally; same inside the built image on EC2 with MinIO as S3 (`S3_ENDPOINT_URL`).
- Done = job reaches `done`, mp4 downloadable from the returned URL, `ffprobe` duration ≈ composition `data-duration`, coverage ≥ 80% on changed files.
