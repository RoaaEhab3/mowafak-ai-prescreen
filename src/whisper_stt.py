"""Batch Whisper speech-to-text.

Loads a local OpenAI Whisper model (base/small, configurable) and transcribes
an audio file in one shot — no real-time streaming.

Ported from the original root-level `whisper_stt.py`: the hardcoded
`C:\\Users\\Administrator\\Downloads\\ffmpeg...` PATH entry was removed. Whisper
needs ffmpeg on PATH; if yours isn't on the system PATH, point the optional
FFMPEG_BIN env var at the directory containing ffmpeg.
"""
from __future__ import annotations

import os
from pathlib import Path

import whisper

# Optional: prepend a custom ffmpeg bin directory if provided.
_ffmpeg_bin = os.environ.get("FFMPEG_BIN")
if _ffmpeg_bin:
    os.environ["PATH"] = _ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")


class WhisperSTT:
    def __init__(self, model_name: str = "base"):
        self.model = whisper.load_model(model_name)

    def transcribe_audio(self, audio_path: str) -> dict:
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        result = self.model.transcribe(audio_path, fp16=False)

        return {
            "transcript": result["text"].strip(),
            "language": result.get("language", "unknown"),
        }
