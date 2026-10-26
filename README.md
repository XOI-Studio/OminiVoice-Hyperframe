# OmniVoice TTS API

`POST /tts` → synthesizes with OmniVoice in one of 12 locked channel voices and returns a public S3 URL.

Voices: `GET /voices` returns each voice with `category` (`narrator` = người kể chính, `character` = nhân vật thoại, `special` = hiệu ứng như ma thì thầm), `channel`, `gender`, `age`, `description`. Optional filter: `GET /voices?category=narrator`.

```bash
curl -X POST localhost:8000/tts -H 'content-type: application/json' -H 'X-API-Key: $API_KEY' \
  -d '{"text":"Đêm thứ ba, tôi nghe tiếng dép ngoài hiên.","voice":"narrator_male","format":"mp3"}'
# {"url":"https://<bucket>.s3.<region>.amazonaws.com/tts/narrator_male/<hash>.mp3","voice":"narrator_male","format":"mp3","duration_s":4.2,"cached":false}
```

Body: `text` (required, ≤ 8000 chars), `voice` (default `narrator_male`), `language` (`vi`), `format` (`wav`|`mp3`), `steps` (16 draft / 32 final), `seed`, `wpm`. Identical requests are served from S3 (`cached: true`).

## Env
`S3_BUCKET` (required), `AWS_REGION`, `S3_PREFIX` (`tts`), `S3_PUBLIC_BASE_URL` (CDN domain, optional), `API_KEY` (optional; sent as `X-API-Key`), `DEVICE` (`cuda`|`cpu`), `DEFAULT_VOICE`, `DEFAULT_STEPS`. AWS credentials come from the instance role (needs `s3:PutObject`, `s3:GetObject` on the prefix).

## Public URLs
The URL is only reachable if the prefix is public-read: disable "Block public access" for the bucket and add a policy allowing `s3:GetObject` on `arn:aws:s3:::<bucket>/tts/*` to `*` (or put CloudFront in front and set `S3_PUBLIC_BASE_URL`).

## AWS hardware
**g4dn.xlarge** (1× T4 16 GB, 4 vCPU, 16 GB RAM, ~US$0.53/h on-demand, ~US$0.16/h Spot) — cheapest GPU that fits OmniVoice fp16. Use the Deep Learning AMI / ECS GPU AMI with nvidia-container-toolkit, 40 GB gp3 disk (image ≈ 12 GB). Step up to g5.xlarge (A10G) only if latency matters. No-GPU fallback: `-e DEVICE=cpu` (very slow).

## Dev
`pip install -r requirements.txt -r requirements-dev.txt && pytest`
