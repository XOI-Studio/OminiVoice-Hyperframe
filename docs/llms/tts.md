# Text to speech API

Synthesize speech from text with a fixed set of voices. The result is uploaded to S3 and returned as a public URL.

This is the TTS service (`tts/`). Video rendering is a separate service with its own base URL: see [render](render.md).

Base URL of the public deployment (Modal, GPU, scales to zero, first request after idle takes about 20 to 30 seconds): `https://jamesngdev--omnivoice-tts-web.us-east.modal.run`

## Authentication
No API key is needed by default, including on the public deployment above. A deployment may set `API_KEY`; then send `X-API-Key: <key>` on every request except `GET /health`, and a missing or wrong key returns `401 {"detail":"invalid API key"}`. Error bodies are always `{"detail": "<message>"}`, except `422` from request-body validation, where `detail` is a list of field errors.

## GET /health
Liveness. No auth. Returns `200 {"status":"ok"}`.

## GET /voices
Lists available voices.
- Query (optional): `category` (`narrator` | `character` | `special`), `channel`. Exact-match filters, combinable.
- Response `200`: object keyed by voice name; each value holds metadata such as `category`, `channel`, `gender`, `age`, `description` (a voice without metadata has `{}`).
- Call this before `POST /tts` to pick a valid `voice`.

```json
{"narrator_male": {"category": "narrator", "channel": "...", "gender": "male", "age": "...", "description": "..."}}
```

## POST /tts
Synthesize `text` with a voice. Idempotent: identical requests return the same file.

Request body:
| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `text` | string | yes | | non-empty after trimming, at most 8000 characters |
| `voice` | string | no | `narrator_male` | must be a name from `GET /voices` |
| `language` | string | no | `vi` | |
| `format` | string | no | `wav` | `wav` or `mp3` |
| `steps` | integer | no | 32 | 4 to 64; 16 is draft quality, 32 is final |
| `seed` | integer | no | null | set for reproducible output |
| `wpm` | number | no | null | greater than 0, at most 400; target words per minute |

Response `200`:
```json
{"url": "https://<bucket>.s3.<region>.amazonaws.com/tts/narrator_male/<hash>.mp3", "voice": "narrator_male", "format": "mp3", "duration_s": 4.2, "cached": false}
```
- `url`: public file. Path is `tts/<voice>/<hash>.<format>`, where the hash derives from text, voice, language, steps, seed, wpm and format. If the server uses a CDN base URL, `url` starts with it instead.
- `cached`: `true` when an identical request already existed; in that case `duration_s` is `null` and no synthesis happens (fast). `false` means it was just synthesized and `duration_s` is the audio length in seconds.
- Synthesis is synchronous and can take seconds for long text; use a generous client timeout.

Errors:
| Status | When | Client action |
|---|---|---|
| 401 | only when the deployment requires a key: wrong or missing key | send the right `X-API-Key` |
| 404 | `unknown voice '<name>'; GET /voices for the list` | call `GET /voices` |
| 413 | `text exceeds 8000 chars` | split the text and make several calls |
| 422 | empty text, or a field fails its constraints (bad `format`, `steps` out of range, `wpm` out of range) | fix the request |

The URL works only if the bucket allows public read on `tts/*` (see [render docs](render.md#s3-layout-and-bucket-policy)).

## Related
- [Video render](render.md): separate service that renders a project whose audio came from `/tts`
- [Index](../../llms.txt)
