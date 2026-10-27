# TTS service

`POST /tts` synthesizes with OmniVoice in one of 12 locked channel voices and returns a public URL. Client reference: [`docs/llms/tts.md`](../docs/llms/tts.md).

Voices: `GET /voices` returns each voice with `category` (`narrator` = người kể chính, `character` = nhân vật thoại, `special` = hiệu ứng như ma thì thầm), `channel`, `gender`, `age`, `description`. Optional filter: `GET /voices?category=narrator`.

```bash
curl -X POST localhost:8000/tts -H 'content-type: application/json' \
  -d '{"text":"Đêm thứ ba, tôi nghe tiếng dép ngoài hiên.","voice":"narrator_male","format":"mp3"}'
# {"url":"https://<bucket>.s3.<region>.amazonaws.com/tts/narrator_male/<hash>.mp3","voice":"narrator_male","format":"mp3","duration_s":4.2,"cached":false}
```

Body: `text` (required, ≤ 8000 chars), `voice` (default `narrator_male`), `language` (`vi`), `format` (`wav`|`mp3`), `steps` (16 draft / 32 final), `seed`, `wpm`. Identical requests are served from storage (`cached: true`). If `API_KEY` is set, add `-H "X-API-Key: $API_KEY"`.

## Layout
`app/` (FastAPI `main.py`, `engine.py` OmniVoice wrapper, `storage.py`, `audio.py`, `text.py`, `config.py`), `voices/` (voice-clone prompts + `voices.json`), `tests/`, `Dockerfile`, `modal_app.py`, `requirements*.txt`.

## Env
`S3_BUCKET` (required), `AWS_REGION`, `S3_PREFIX` (`tts`), `S3_PUBLIC_BASE_URL`, `S3_ENDPOINT_URL`, `API_KEY` (optional), `DEVICE` (`cuda`|`cpu`), `DEFAULT_VOICE`, `DEFAULT_STEPS`, `MAX_TEXT_CHARS`. On AWS the credentials come from the instance role (`s3:PutObject`, `s3:GetObject` on `tts/*`).

## Docker (GPU)
```bash
docker buildx build --platform linux/amd64 -t omnivoice-tts .        # from this folder; image ≈ 15 GB with the model baked in
docker run --gpus all -p 8000:8000 --env-file ../.env omnivoice-tts
```
**AWS hardware:** g4dn.xlarge (1× T4 16 GB, 4 vCPU, 16 GB RAM, ~US$0.53/h on-demand, ~US$0.16/h Spot) is the cheapest GPU that fits OmniVoice fp16; 40 GB gp3 disk. No-GPU fallback: `-e DEVICE=cpu` (about 3× slower than real time on 16 vCPU, measured).

## Modal (serverless GPU)
`modal_app.py` serves this same API on Modal: GPU only while a request runs, scales to zero, billed per second. Step-by-step guide (account, billing, secret, deploy, test, troubleshooting): see **"Deploy TTS lên Modal"** in the [root README](../README.md#deploy-tts-lên-modal-hướng-dẫn-chi-tiết). Short version: `modal secret create omnivoice-tts --from-dotenv ...` then `modal deploy tts/modal_app.py` from the repo root.

## Tests
`pip install -r requirements.txt -r requirements-dev.txt && pytest` (inside this folder).
