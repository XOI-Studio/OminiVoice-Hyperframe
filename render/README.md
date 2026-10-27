# Render service

`POST /render` renders a HyperFrames project stored in S3 to mp4 (async job) and returns a public URL. Client reference: [`docs/llms/render.md`](../docs/llms/render.md). Audio, images and scenes are already in the S3 folder; this only renders (use the [TTS service](../tts/README.md) to produce audio).

```bash
curl -X POST localhost:8000/render -H 'content-type: application/json' \
  -d '{"folder":"projects/ep01","name":"ep01","quality":"looks","fps":30}'
# 202 {"job_id":"3f9c…","status":"queued","status_url":"/jobs/3f9c…"}
curl localhost:8000/jobs/3f9c…
# {"status":"done","url":"https://<bucket>.s3.<region>.amazonaws.com/renders/projects/ep01/ep01-3f9c1a2b.mp4",…}
```
If `API_KEY` is set, add `-H "X-API-Key: $API_KEY"` to both calls.

- `folder`: S3 key in `SOURCE_BUCKET` (default `S3_BUCKET`). A `.zip` key is downloaded and unzipped; anything else is a prefix whose objects (except `renders/`) are downloaded. The HyperFrames project (`index.html`, `compositions/`, `assets/`) is at the root or in the single top-level directory (macOS `__MACOSX` entries are ignored).
- Body: `folder` (required), `name` (`out`), `quality` (`draft`|`looks`|`delivery`), `fps` (24|30|60), `format` (`mp4`), `workers` (`auto` or 1–8).
- Status: `queued → downloading → rendering → uploading → done | failed` (`error` and `log_tail` on failure, `render_s` is the render wall-clock seconds). State lives in `s3://<bucket>/jobs/<id>.json`; a job left unfinished by a restart reads as `failed: interrupted`.
- Errors: 401 bad key (only when `API_KEY` is set) · 404 source not found / unknown job · 422 invalid folder or option · 429 queue full · 503 render not configured / storage unavailable. One render at a time.
- Only render folders you trust: compositions run arbitrary HTML/JS in Chrome. The render process gets a minimal environment (no `API_KEY` or cloud credentials).

## Layout
`app/` (FastAPI `main.py`, `jobs.py` queue + worker thread + S3 job state, `source.py` safe download/unzip, `runner.py` CLI process with timeout, `validation.py`, `storage.py`, `config.py`), `tests/` (with a real fixture project), `Dockerfile`, `requirements*.txt`.

## Env
`S3_BUCKET` (required; mp4 and job state go here), `SOURCE_BUCKET` (default `S3_BUCKET`), `AWS_REGION`, `S3_PUBLIC_BASE_URL`, `S3_ENDPOINT_URL`, `API_KEY` (optional), `RENDER_PREFIX` (`renders`), `RENDER_TIMEOUT_S` (7200), `RENDER_MAX_DOWNLOAD_MB` (4096), `RENDER_MAX_FILES` (5000), `RENDER_QUEUE_MAX` (10). On AWS the credentials come from the instance role: `s3:PutObject`/`s3:GetObject` on `renders/*` and `jobs/*`, plus `s3:GetObject` and `s3:ListBucket` on the source folders.

## Docker (CPU only)
```bash
docker buildx build --platform linux/amd64 -t hyperframes-render .   # from this folder; no GPU, PyTorch or model in the image
docker run --shm-size=1g -p 8000:8000 --env-file ../.env hyperframes-render
```
`--shm-size=1g` is required (Chrome crashes on the 64 MB default `/dev/shm`). Rendering uses CPU only: give it vCPUs (`workers`) and ~256 MB RAM each. The image runs as a non-root user under tini and carries Node 22, ffmpeg, HyperFrames 0.8.58 and a pinned Chrome headless shell.

## Operations
- Set an S3 lifecycle rule to expire `jobs/` objects after 30 days.
- On EC2 require IMDSv2 (`HttpTokens=required`): the render process runs project code, so the metadata endpoint must not be reachable by a plain GET.
- Result URLs open only if `renders/*` is publicly readable (see the root README); keep `jobs/*` and sources private.

## Tests
`pip install -r requirements.txt -r requirements-dev.txt && pytest` (inside this folder). `HF_INTEGRATION=1 pytest tests/test_integration.py` runs three real renders and needs the `hyperframes` CLI (or `npx`), ffmpeg and Chrome.
