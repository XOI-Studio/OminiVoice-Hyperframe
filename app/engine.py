import json
import threading
from pathlib import Path

import numpy as np

from . import config
from .text import split_text


class Engine:
    """OmniVoice wrapper: lazy model load, one inference at a time, voice-clone prompts cached per voice."""

    def __init__(self, voices_dir: Path = config.VOICES_DIR):
        self.voices_dir = voices_dir
        self._model = None
        self._prompts: dict = {}
        self._lock = threading.Lock()
        meta = voices_dir / "voices.json"
        self.meta = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}

    def voices(self) -> dict:
        names = sorted(p.name.removesuffix(".prompt.pt") for p in self.voices_dir.glob("*.prompt.pt"))
        return {n: self.meta.get(n, {}) for n in names}

    @property
    def sampling_rate(self) -> int:
        return self._model.sampling_rate if self._model else 24000

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from omnivoice import OmniVoice

        dtype = torch.float16 if config.DEVICE == "cuda" else torch.float32
        self._model = OmniVoice.from_pretrained(config.MODEL_ID, device_map=config.DEVICE, dtype=dtype)

    def synthesize(self, text: str, voice: str, language: str, steps: int, seed: int | None, wpm: float | None) -> tuple[np.ndarray, int]:
        import torch
        from omnivoice.models.omnivoice import OmniVoiceGenerationConfig, VoiceClonePrompt

        with self._lock:
            self.load()
            if voice not in self._prompts:
                self._prompts[voice] = VoiceClonePrompt.load(str(self.voices_dir / f"{voice}.prompt.pt"))
            cfg = OmniVoiceGenerationConfig(num_step=steps)
            sr = self._model.sampling_rate
            tail = np.zeros(int(config.TAIL_SECONDS * sr), np.float32)
            parts = []
            for i, chunk in enumerate(split_text(text, config.CHUNK_CHARS)):
                if seed is not None:
                    torch.manual_seed(seed + i)
                audio = self._model.generate(
                    text=chunk, language=language, voice_clone_prompt=self._prompts[voice], generation_config=cfg,
                    duration=len(chunk.split()) / wpm * 60 if wpm else None,
                )[0]
                parts += [np.asarray(audio, dtype=np.float32).reshape(-1), tail]
            if config.DEVICE == "cuda":
                torch.cuda.empty_cache()
            return np.concatenate(parts), sr
