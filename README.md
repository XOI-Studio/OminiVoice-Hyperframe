# OmniVoice TTS + HyperFrames render API

`POST /tts` → synthesizes with OmniVoice in one of 12 locked channel voices and returns a public S3 URL.
`POST /render` → renders a HyperFrames project stored on S3 to mp4 (async job) and returns a public S3 URL.

Voices: `GET /voices` returns each voice with `category` (`narrator` = người kể chính, `character` = nhân vật thoại, `special` = hiệu ứng như ma thì thầm), `channel`, `gender`, `age`, `description`. Optional filter: `GET /voices?category=narrator`.

```bash
curl -X POST localhost:8000/tts -H 'content-type: application/json' -H 'X-API-Key: $API_KEY' \
  -d '{"text":"Đêm thứ ba, tôi nghe tiếng dép ngoài hiên.","voice":"narrator_male","format":"mp3"}'
# {"url":"https://<bucket>.s3.<region>.amazonaws.com/tts/narrator_male/<hash>.mp3","voice":"narrator_male","format":"mp3","duration_s":4.2,"cached":false}
```

Body: `text` (required, ≤ 8000 chars), `voice` (default `narrator_male`), `language` (`vi`), `format` (`wav`|`mp3`), `steps` (16 draft / 32 final), `seed`, `wpm`. Identical requests are served from S3 (`cached: true`).

## Render (HyperFrames)
Audio, images and scenes are already in the S3 folder; this only renders.

```bash
curl -X POST localhost:8000/render -H 'content-type: application/json' -H "X-API-Key: $API_KEY" \
  -d '{"folder":"projects/ep01","name":"ep01","quality":"looks","fps":30}'
# 202 {"job_id":"3f9c…","status":"queued","status_url":"/jobs/3f9c…"}
curl localhost:8000/jobs/3f9c… -H "X-API-Key: $API_KEY"
# {"status":"done","url":"https://<bucket>.s3.<region>.amazonaws.com/renders/projects/ep01/ep01-3f9c1a2b.mp4",…}
```

- `folder`: S3 key in `SOURCE_BUCKET` (default `S3_BUCKET`). A `.zip` key is downloaded and unzipped; anything else is a prefix whose objects (except `renders/`) are downloaded. The HyperFrames project (`index.html`, `compositions/`, `assets/`) is at the root or in the single top-level directory.
- Body: `folder` (required), `name` (`out`), `quality` (`draft`|`looks`|`delivery`), `fps` (24|30|60), `format` (`mp4`), `workers` (`auto` or 1–8).
- Status: `queued → downloading → rendering → uploading → done | failed` (`error` and `log_tail` on failure). State lives in `s3://<bucket>/jobs/<id>.json`; a job left unfinished by a restart reads as `failed: interrupted`.
- Errors: 401 bad key · 404 source not found / unknown job · 422 invalid folder or option · 429 queue full · 503 render not configured / storage unavailable. One render at a time.
- Env: `SOURCE_BUCKET`, `RENDER_PREFIX` (`renders`), `RENDER_TIMEOUT_S` (7200), `RENDER_MAX_DOWNLOAD_MB` (4096), `RENDER_MAX_FILES` (5000), `RENDER_QUEUE_MAX` (10).
- Run the container with `--shm-size=1g` (Chrome crashes on the 64 MB default `/dev/shm`). Rendering uses CPU only; give it vCPUs (`workers`) and ~256 MB RAM each.
- Only render folders you trust: compositions run arbitrary HTML/JS in Chrome.

## Env
`S3_BUCKET` (required), `AWS_REGION`, `S3_PREFIX` (`tts`), `S3_PUBLIC_BASE_URL` (CDN domain, optional), `API_KEY` (optional; sent as `X-API-Key`), `DEVICE` (`cuda`|`cpu`), `DEFAULT_VOICE`, `DEFAULT_STEPS`. AWS credentials come from the instance role (needs `s3:PutObject`, `s3:GetObject` on `tts/*`, `renders/*`, `jobs/*`, plus `s3:GetObject` and `s3:ListBucket` on the source folders).

## Public URLs
The URL is only reachable if the prefix is public-read: disable "Block public access" for the bucket and add a policy allowing `s3:GetObject` on `arn:aws:s3:::<bucket>/tts/*` and `arn:aws:s3:::<bucket>/renders/*` to `*` (keep `jobs/*` and your sources private) (or put CloudFront in front and set `S3_PUBLIC_BASE_URL`).

## Operations
- Set an S3 lifecycle rule to expire `jobs/` objects after 30 days.
- On EC2 require IMDSv2 (`HttpTokens=required`): the render process runs project code, so the metadata endpoint must not be reachable by a plain GET.
- The image runs under tini (ENTRYPOINT) to reap Chrome/ffmpeg zombies; keep it when overriding CMD.

## AWS hardware
**g4dn.xlarge** (1× T4 16 GB, 4 vCPU, 16 GB RAM, ~US$0.53/h on-demand, ~US$0.16/h Spot) — cheapest GPU that fits OmniVoice fp16. Use the Deep Learning AMI / ECS GPU AMI with nvidia-container-toolkit, 40 GB gp3 disk (image ≈ 12 GB). Step up to g5.xlarge (A10G) only if latency matters. No-GPU fallback: `-e DEVICE=cpu` (very slow).

## Dev
`pip install -r requirements.txt -r requirements-dev.txt && pytest`
