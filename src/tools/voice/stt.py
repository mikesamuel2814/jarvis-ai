"""
JARVIS Speech-to-Text (STT)
Real-time transcription using faster-whisper.
Integrates with v3 tools/ ecosystem.
"""
import os
import wave
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

JARVIS_DIR = Path("/home/kali/.jarvis")
DEFAULT_MODEL_PATH = "/home/kali/faster-whisper-tiny"


class STTEngine:
    """Speech-to-text engine using faster-whisper."""

    def __init__(self, model_path: str = DEFAULT_MODEL_PATH, device: str = "cuda", compute_type: str = "float16"):
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_path, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio_path: str, language: Optional[str] = None, vad_filter: bool = True) -> List[Dict[str, Any]]:
        """Transcribe an audio file to text with timestamps.

        Args:
            audio_path: Path to audio file (wav, mp3, etc.)
            language: Optional language code (e.g., 'en')
            vad_filter: Use voice activity detection to filter silence

        Returns:
            List of segments with start, end, text
        """
        model = self._load_model()
        segments, info = model.transcribe(audio_path, language=language, vad_filter=vad_filter)

        results = []
        for segment in segments:
            results.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "confidence": segment.avg_logprob
            })
        return results

    def transcribe_microphone_chunk(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe raw audio bytes (e.g., from microphone chunk).

        Args:
            audio_bytes: Raw PCM audio bytes
            sample_rate: Sample rate of audio (default 16000)

        Returns:
            Transcribed text
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            with wave.open(f, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio_bytes)
            temp_path = f.name

        try:
            segments = self.transcribe(temp_path)
            return " ".join([s["text"] for s in segments])
        finally:
            os.unlink(temp_path)


def jarvis_transcribe(audio_path: str, language: str = "en") -> Dict[str, Any]:
    """Tool wrapper for JARVIS tool registry."""
    engine = STTEngine()
    segments = engine.transcribe(audio_path, language=language)
    return {
        "status": "success",
        "text": " ".join([s["text"] for s in segments]),
        "segments": segments
    }


if __name__ == "__main__":
    # Quick test
    engine = STTEngine()
    print("STT Engine initialized")
    print(f"Model path: {engine.model_path}")
    print(f"Device: {engine.device}")
