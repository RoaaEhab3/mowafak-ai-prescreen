import os

os.environ["PATH"] = (
    r"C:\Users\Administrator\Downloads\ffmpeg-8.1.1-essentials_build\ffmpeg-8.1.1-essentials_build\bin"
    + os.pathsep
    + os.environ["PATH"]
)

from pathlib import Path
import whisper


class WhisperSTT:

    def __init__(self, model_name="base"):
        self.model = whisper.load_model(model_name)

    def transcribe_audio(self, audio_path: str):

        if not Path(audio_path).exists():
            raise FileNotFoundError(
                f"Audio file not found: {audio_path}"
            )

        result = self.model.transcribe(
            audio_path,
            fp16=False
        )

        return {
            "transcript": result["text"].strip(),
            "language": result.get("language", "unknown")
        }