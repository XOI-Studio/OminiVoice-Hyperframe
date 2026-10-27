# Video render API

Render a HyperFrames project stored in S3 into an mp4. Rendering takes minutes, so it is an asynchronous job: submit, then poll. The project (including its audio) must already be in S3; this API does not generate audio (use [POST /tts](tts.md#post-tts) separately).

## Flow
1. `POST /render` with the S3 location of the project. Source existence is checked immediately; on success you get `202` and a `job_id`.
2. Poll `GET /jobs/{job_id}` (every few seconds) until `status` is `done` or `failed`.
3. On `done`, download `url` (public mp4).

Jobs run one at a time in submission order. At most 10 jobs may be unfinished (queued or running) at once.

## Input folder forms
`folder` is an S3 key in the source bucket. Two forms:
- Prefix, e.g. `projects/ep01` (a trailing `/` is ignored): every object under that prefix is downloaded, except anything under `<prefix>/renders/`.
- Zip, a key ending in `.zip` (lowercase), e.g. `projects/ep01.zip`: downloaded and unzipped.

The project root must contain `index.html`, either directly at the root of the prefix/zip, or inside exactly one top-level directory (and that directory is then used as root). Anything else fails the job with `index.html not found`.

`folder` rules: 1 to 512 characters after trimming, only `A-Z a-z 0-9 . _ / space -`, no leading `/`, no `//`, no backslash, no `..` path segment. Violations return 422.

## POST /render
Queue a render job. Auth required. Not idempotent: each call creates a new job.

Body:
| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `folder` | string | yes | | see above |
| `name` | string | no | `out` | 1 to 64 chars of `A-Z a-z 0-9 . _ -`; used in the output file name |
| `quality` | string | no | `looks` | `draft`, `looks`, `delivery` |
| `fps` | integer | no | 30 | 24, 30 or 60 |
| `format` | string | no | `mp4` | only `mp4` |
| `workers` | string or integer | no | `auto` | `auto` or an integer 1 to 8 (parallel render workers; more is faster but needs more CPU and about 256 MB RAM each) |

Response `202`:
```json
{"job_id": "af79babd0c1d4e6f8a2b3c4d5e6f7a8b", "status": "queued", "status_url": "/jobs/af79babd0c1d4e6f8a2b3c4d5e6f7a8b"}
```
`job_id` is 32 lowercase hex characters.

Errors:
| Status | Body detail | When | Client action |
|---|---|---|---|
| 401 | `invalid API key` | wrong or missing key | fix the key |
| 404 | `source not found` | prefix has no objects (outside `renders/`) or the zip key does not exist in the source bucket | check `folder` |
| 422 | message, or field error list | invalid folder, name, quality, fps, format or workers | fix the request |
| 429 | `render queue full` | 10 unfinished jobs already | retry later, after polling shows jobs finishing |

A source that exists but is not a valid project is accepted (202) and fails later in the job (see Job errors).

## GET /jobs/{job_id}
Returns the current state of a job. Auth required. Safe to poll.

Response `200`:
```json
{
  "job_id": "af79babd0c1d4e6f8a2b3c4d5e6f7a8b", "status": "done",
  "folder": "projects/ep01", "name": "ep01", "quality": "looks", "fps": 30, "workers": "auto",
  "url": "https://<bucket>.s3.ap-southeast-2.amazonaws.com/renders/projects/ep01/ep01-af79babd.mp4",
  "render_s": 312.4, "error": null, "log_tail": null,
  "created_at": "2026-10-27T01:00:00+00:00", "updated_at": "2026-10-27T01:05:12+00:00"
}
```
- `status`: `queued` then `downloading`, `rendering`, `uploading`, then `done` or `failed`. `done` and `failed` are final.
- `url`: set only when `done`; otherwise `null`. If the server uses a CDN base URL, it starts with that.
- `render_s`: seconds spent rendering (not video length); set from `uploading` onward, `null` before and on failures.
- `error`: set when `failed`. `log_tail`: last up to 4 KB of renderer output, set on timeout, non-zero exit or empty output; otherwise `null`.
- `created_at`, `updated_at`: ISO 8601 UTC.

Errors: `401` bad key; `404 job not found` for an unknown id or an id that is not 32 lowercase hex characters.

### The interrupted rule
If the service restarts while a job is unfinished, that job can no longer progress. A later `GET` reports it as `status: "failed"` with `error: "interrupted (service restarted)"`. Resubmit with `POST /render` to retry.

### Job errors (`status: failed`)
`error` values you may see:
- `index.html not found`: project root not found (see input forms).
- `source not found: <folder>`: the source was deleted after queuing.
- `zip exceeds <n> bytes`, `project exceeds <n> bytes`, `zip uncompressed size exceeds ...`, `zip has more than <n> entries`, `more than <n> objects under '<prefix>'`: download caps exceeded.
- `invalid zip: ...`, `unsafe path in zip: ...`, `symlink in zip: ...`, `unsafe key: ...`: unsafe or corrupt input.
- `render timed out after <n>s`: exceeded the timeout.
- `hyperframes exited <code>`: the renderer failed; read `log_tail`.
- `render produced no output`.
- `internal error: <type>`: unexpected server failure.

## Limits
Server defaults (operators can change them):
- Queue: 10 unfinished jobs; the 11th gets 429.
- Render timeout: 7200 s per job.
- Download cap: 4096 MB (prefix total, zip file size, and zip uncompressed size).
- File cap: 5000 objects or zip entries.
- Options: quality `draft|looks|delivery`, fps `24|30|60`, workers `auto` or 1 to 8.

## S3 layout and bucket policy
In the output bucket:
- `tts/<voice>/<hash>.<wav|mp3>`: speech files.
- `renders/<folder-without-.zip>/<name>-<id8>.mp4`: render output; `<id8>` is the first 8 characters of `job_id`. Example: `folder: "projects/ep01"`, `name: "ep01"` gives `renders/projects/ep01/ep01-af79babd.mp4`; `folder: "projects/ep01.zip"` gives `renders/projects/ep01/ep01-<id8>.mp4`. Public URL: `https://<bucket>.s3.ap-southeast-2.amazonaws.com/renders/projects/ep01/ep01-af79babd.mp4` (region per deployment).
- `jobs/<job_id>.json`: job state (what `GET /jobs/{job_id}` returns). Keep private.

Source projects are read from the source bucket (the output bucket unless configured otherwise) and stay private; renders are written under `renders/`, never beside the source.

Bucket policy requirement: public `s3:GetObject` for `tts/*` and `renders/*` only, with "Block public access" disabled for those policies; do not expose `jobs/*` or sources. A CDN in front of the bucket is an alternative; then returned URLs use the CDN domain.

Only submit projects you trust: compositions run arbitrary HTML/JS during rendering.

## Related
- [Text to speech](tts.md): generate the audio that goes into a project
- [Index](../../llms.txt)
