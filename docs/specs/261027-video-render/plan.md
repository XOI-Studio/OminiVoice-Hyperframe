# Plan: POST /render — render a HyperFrames project from S3
**Design:** docs/specs/261027-video-render/design.md · **Size:** large · **Base:** feat/omnivoice-tts-api

## File map
- `app/config.py` (modify) — render env vars (`SOURCE_BUCKET`, `RENDER_PREFIX`, `RENDER_TIMEOUT_S`, `RENDER_MAX_DOWNLOAD_MB`, `RENDER_MAX_FILES`, `RENDER_QUEUE_MAX`, `HYPERFRAMES_BIN`).
- `app/render/__init__.py` (create)
- `app/render/validation.py` (create) — folder/name validation, option allowlist → CLI args. Pure.
- `app/render/source.py` (create) — fetch project from S3 (prefix or zip) into a temp dir with guards; find project root.
- `app/render/runner.py` (create) — run the `hyperframes render` subprocess: timeout, process-group kill, log tail.
- `app/render/jobs.py` (create) — `Job`, S3-backed `JobStore`, in-process queue + worker thread, orchestration, interrupted detection.
- `app/storage.py` (modify) — `list_keys`, `download_file`, `upload_file`, `get_json`, `put_json`, `head_size`; takes bucket per call (source vs output bucket).
- `app/main.py` (modify) — `POST /render`, `GET /jobs/{job_id}`, start worker on startup.
- `tests/test_render_validation.py`, `test_render_source.py`, `test_render_runner.py`, `test_render_jobs.py`, `test_render_api.py`, `test_storage.py` (create); `tests/test_render_integration.py` (create, runs only with `HF_INTEGRATION=1`).
- `tests/fixtures/mini-project/` (create) — 2 s HyperFrames project for the integration/smoke render.
- `Dockerfile` (modify) — Node 22, pinned `hyperframes`, Chrome shared libs + baked Chrome, non-root user.
- `README.md` (modify) — `/render` usage, bucket policy for `renders/*`, `--shm-size`.
- `llms.txt`, `docs/llms/render.md`, `docs/llms/tts.md` (create via `/sw:llms-txt`) — client-facing API docs.

## Tasks
### T1: Request validation and CLI args
- Files: `app/render/validation.py`, `tests/test_render_validation.py`
- Interface: `validate_folder(folder: str) -> str` (strips, raises `ValueError`); `validate_name(name) -> str`; `cli_args(quality, fps, workers, output: Path) -> list[str]` → `["render","--quality",q,"--fps",str(fps),"--workers",str(w),"--output",str(output)]`; constants `QUALITIES=("draft","looks","delivery")`, `FPS=(24,30,60)`, workers `auto|1..8`.
- TDD: test-first
- Tests: `it('rejects empty folder')`, `it('rejects .. segments and leading slash')`, `it('rejects chars outside [A-Za-z0-9._/ -]')`, `it('accepts nested prefix and .zip key')`, `it('rejects name longer than 64 or with slash')`, `it('maps options to hyperframes render args')`, `it('rejects fps 25 and quality ultra and workers 99')`
- Done when: `pytest tests/test_render_validation.py -q` passes
- [ ] write failing test · [ ] run (red) · [ ] implement · [ ] run (green) · [ ] commit

### T2: Storage helpers
- Files: `app/storage.py`, `tests/test_storage.py`
- Interface: `S3Storage.list_keys(bucket, prefix, limit) -> list[tuple[str,int]]` (key,size; raises `TooManyObjects`); `download_file(bucket,key,dest: Path)`; `upload_file(bucket,key,src: Path,content_type)`; `get_json(bucket,key) -> dict|None`; `put_json(bucket,key,obj)`; `head_size(bucket,key) -> int|None`. Existing `exists/put/url` keep working.
- TDD: test-first (botocore `Stubber` / fake client)
- Tests: `it('lists all pages of a prefix')`, `it('raises when more than limit objects')`, `it('get_json returns None on NoSuchKey')`, `it('upload_file sets content type and cache-control')`, `it('head_size returns None when missing')`
- Done when: `pytest tests/test_storage.py tests/test_api.py -q` passes (existing `/tts` tests unchanged)
- [ ] write failing test · [ ] run (red) · [ ] implement · [ ] run (green) · [ ] commit

### T3: Fetch project from S3
- Files: `app/render/source.py`, `tests/test_render_source.py`
- Interface: `fetch_project(storage, bucket, folder, dest: Path, max_bytes: int, max_files: int) -> Path` (project root). Prefix → download every key except under `<prefix>/renders/`; `.zip` → download then safe-extract. Root = `dest` if it holds `index.html`, else its single top-level dir holding `index.html`, else `ProjectError("index.html not found")`. `ProjectError`/`SourceNotFound` subclasses of `Exception`.
- TDD: test-first
- Tests: `it('downloads a prefix without renders/')`, `it('extracts a zip and finds the project in a single subfolder')`, `it('rejects zip-slip entries')`, `it('rejects symlink entries')`, `it('rejects zip over max uncompressed bytes (bomb)')`, `it('rejects more than max_files')`, `it('raises SourceNotFound for empty prefix or missing zip')`, `it('raises ProjectError when no index.html')`
- Done when: `pytest tests/test_render_source.py -q` passes
- [ ] write failing test · [ ] run (red) · [ ] implement · [ ] run (green) · [ ] commit

### T4: Runner
- Files: `app/render/runner.py`, `tests/test_render_runner.py`
- Interface: `run_render(root: Path, args: list[str], timeout_s: int, on_line: Callable[[str],None]|None = None, bin: str = config.HYPERFRAMES_BIN) -> RunResult(returncode:int, log_tail:str, timed_out:bool)`. Starts in its own process group, `cwd=root`, merged stdout/stderr, keeps last 4 KB, kills the group on timeout.
- TDD: test-first (use a tiny shell script as `bin`)
- Tests: `it('returns 0 and log tail on success')`, `it('returns non-zero code and the error text')`, `it('kills the process group and flags timeout')`, `it('keeps only the last 4096 bytes')`, `it('runs in the project root')`
- Done when: `pytest tests/test_render_runner.py -q` passes
- [ ] write failing test · [ ] run (red) · [ ] implement · [ ] run (green) · [ ] commit

### T5: Jobs, queue, worker
- Files: `app/render/jobs.py`, `tests/test_render_jobs.py`
- Interface: `JobManager(storage, fetch=fetch_project, run=run_render)`; `submit(folder,name,quality,fps,workers) -> Job` (checks source exists, raises `QueueFull`, `SourceNotFound`); `get(job_id) -> dict|None`; `start()/stop()` worker thread. Job JSON in `s3://S3_BUCKET/jobs/<id>.json`: `{job_id,status,folder,name,url,render_s,error,log_tail,created_at,updated_at}`. Output key `renders/<folder-without-.zip>/<name>-<id8>.mp4`, public URL via `storage.url`. Non-terminal status with id not in this process → returned as `failed` / `interrupted (service restarted)`. Temp dir removed in `finally`; missing/empty mp4 → `failed`.
- TDD: test-first (fake storage, fake fetch/run)
- Tests: `it('walks queued→downloading→rendering→uploading→done and returns the url')`, `it('fails with the log tail when hyperframes exits non-zero')`, `it('fails on timeout')`, `it('fails when output mp4 is missing or empty')`, `it('cleans the temp dir on success and failure')`, `it('raises QueueFull past RENDER_QUEUE_MAX')`, `it('raises SourceNotFound before queueing')`, `it('reports interrupted for a stale non-terminal job')`, `it('a worker crash marks the job failed and keeps the worker alive')`, `it('runs one job at a time')`
- Done when: `pytest tests/test_render_jobs.py -q` passes
- [ ] write failing test · [ ] run (red) · [ ] implement · [ ] run (green) · [ ] commit

### T6: API endpoints
- Files: `app/main.py`, `app/config.py`, `tests/test_render_api.py`
- Interface: `POST /render {folder, name="out", quality="looks", fps=30, format="mp4", workers="auto"}` → `202 {job_id,status:"queued",status_url:"/jobs/<id>"}` | `401` | `404 source not found` | `422` | `429 queue full`. `GET /jobs/{job_id}` → job JSON | `404`. Both behind `check_key`. `get_jobs()` dependency like `get_engine`; worker started in the existing `startup` hook only when `S3_BUCKET` is set.
- TDD: integration (TestClient with dependency overrides)
- Tests: `it('POST /render returns 202 and a status_url')`, `it('rejects bad folder/quality/fps with 422')`, `it('returns 404 when the source is missing')`, `it('returns 429 when the queue is full')`, `it('requires X-API-Key when configured')`, `it('GET /jobs/{id} returns the job and 404 for unknown')`, `it('/tts still works')`
- Done when: `pytest -q` passes (all suites)
- [ ] write failing test · [ ] run (red) · [ ] implement · [ ] run (green) · [ ] commit

### T7: Image and docs
- Files: `Dockerfile`, `README.md`
- Interface: Node 22 from NodeSource; `ARG HYPERFRAMES_VERSION=0.8.58`, `npm i -g hyperframes@$HYPERFRAMES_VERSION`; install Chrome shared libs (libnss3, libatk1.0-0, libgbm1, libasound2, fonts-noto etc.) and `hyperframes browser ensure` as the runtime user; `USER app`; `HYPERFRAMES_BIN=hyperframes`; README documents `docker run --shm-size=1g`, `SOURCE_BUCKET`, bucket policy for `renders/*`, request examples.
- TDD: none
- Tests: covered by T8 (`hyperframes doctor` inside the image reports Node, FFmpeg, Chrome ok)
- Done when: `hyperframes doctor --json` inside the built image has Node/FFmpeg/FFprobe/Chrome `ok: true`
- [ ] edit · [ ] commit

### T8: Real render proof (local + image)
- Files: `tests/fixtures/mini-project/` (index.html, assets/ with one image + one audio, `hyperframes.json`), `tests/test_render_integration.py`
- Interface: with `HF_INTEGRATION=1`, upload the fixture to a MinIO/fake S3 prefix, run `JobManager` end to end with the real CLI at `--quality draft`.
- TDD: integration
- Tests: `it('renders the mini project to a playable mp4 whose duration matches data-duration')`
- Done when: `HF_INTEGRATION=1 pytest tests/test_render_integration.py -q` passes locally and the same render succeeds inside the image on EC2 against MinIO
- [ ] write test · [ ] run local · [ ] build image on EC2 · [ ] run in image · [ ] commit

### T9: llms-txt sync (mandatory stage 8)
- Files: `llms.txt`, `docs/llms/render.md`, `docs/llms/tts.md`
- Interface: `/sw:llms-txt` — document `/tts`, `/voices`, `/render`, `/jobs/{id}`: auth header, request/response shapes, error codes, limits, async flow, S3 layout (`tts/`, `renders/`, `jobs/`), bucket-policy requirement.
- TDD: none
- Done when: `llms.txt` links resolve to the topic files and every endpoint/error code in `app/main.py` appears in the docs
- [ ] run `/sw:llms-txt` · [ ] commit

## Verify
lint (none configured → `python -m compileall app`) · typecheck (none) · unit+API `pytest -q --cov=app` (≥ 80% on changed files) · integration `HF_INTEGRATION=1 pytest tests/test_render_integration.py` · build `docker buildx build --platform linux/amd64 .` on EC2 x86 · dev server: run the image with MinIO · smoke: `POST /render` with the fixture prefix → poll `/jobs/{id}` → `done`; download `url`, `ffprobe` duration ≈ composition `data-duration`; cases: missing folder 404, bad folder 422, wrong key 401, queue full 429, bad zip → `failed` with reason · playwright: n/a (no frontend).

## Rulings
Red-team (large):
- Ruling: job ids are `uuid4().hex`, job JSON stays under non-public `jobs/` — status is unguessable and not covered by the public-read policy — cost if wrong: a leaked id exposes only status/log tail.
- Ruling: stale non-terminal jobs are reported as `failed: interrupted` at read time, not by a startup scan — avoids listing S3 on boot — cost: the S3 JSON stays stale until read.
- Ruling: zip extraction counts declared sizes up front and enforces the byte cap again while writing; symlinks and absolute/`..` names are rejected — protects against bombs/zip-slip — cost: legit zips with symlinks fail.
- Ruling: Chrome zip not checksum-pinned — pinned version over https from Google storage — risk accepted for now.
- Ruling: `format` accepts only `mp4` (no webm/gif) — YAGNI — cost: add later via allowlist.
- Ruling: TTS and render may run at the same time (separate locks) — blocking would stall `/tts` for an hour — cost: slower renders while synthesising on 4 vCPU; revisit if seen.
- Ruling: Chrome sandbox/`/dev/shm` in containers are unknown until T8 — docs require `--shm-size=1g`; if Chrome refuses to start as non-root, pass the flag HyperFrames documents or run the worker as root in the container — cost: one Dockerfile tweak.
- Ruling: skipping a separate `sw:discover` stage — research is folded into design.md Context.
