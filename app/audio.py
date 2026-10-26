import io
import subprocess

import numpy as np
import soundfile as sf


def encode(audio: np.ndarray, sr: int, fmt: str) -> tuple[bytes, str]:
    """Return (bytes, content-type) for fmt in {wav, mp3}."""
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    wav = buf.getvalue()
    if fmt == "wav":
        return wav, "audio/wav"
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-codec:a", "libmp3lame", "-q:a", "2", "-f", "mp3", "pipe:1"],
        input=wav, capture_output=True, check=True,
    )
    return out.stdout, "audio/mpeg"
