"""
JARVIS Voice Activity Detection (VAD)
Real-time speech detection using Silero VAD.
Integrates with v3 tools/ ecosystem.
"""
import torch
import numpy as np
from typing import List, Dict, Any, Optional
from pathlib import Path

JARVIS_DIR = Path("/home/kali/.jarvis")


class VADEngine:
    """Voice Activity Detection using Silero VAD."""

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._model = None
        self._utils = None

    def _load_model(self):
        if self._model is None:
            self._model, self._utils = torch.hub.load(
                'snakers4/silero-vad',
                'silero_vad',
                trust_repo=True,
                force_reload=False
            )
        return self._model, self._utils

    def detect_speech(self, audio: np.ndarray, return_timestamps: bool = False) -> Dict[str, Any]:
        """Detect speech in audio array.

        Args:
            audio: Audio samples as numpy array (float32, normalized -1 to 1)
            return_timestamps: Return speech timestamps

        Returns:
            Dict with is_speech, confidence, timestamps
        """
        model, utils = self._load_model()
        get_speech_timestamps, _, _, _, _ = utils

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Normalize if int16
        if audio.max() > 1.0:
            audio = audio / 32768.0

        wav = torch.from_numpy(audio)

        if return_timestamps:
            timestamps = get_speech_timestamps(wav, model, threshold=self.threshold, sampling_rate=self.sample_rate)
            return {
                "is_speech": len(timestamps) > 0,
                "timestamps": timestamps,
                "threshold": self.threshold
            }
        else:
            # Quick check: sample a few chunks
            chunk_size = 512 if self.sample_rate == 16000 else 256
            total_chunks = min(30, len(audio) // chunk_size)
            if total_chunks == 0:
                return {"is_speech": False, "confidence": 0.0}

            probs = []
            for i in range(total_chunks):
                chunk = wav[i * chunk_size:(i + 1) * chunk_size]
                if len(chunk) == chunk_size:
                    prob = model(chunk, self.sample_rate).item()
                    probs.append(prob)

            avg_prob = sum(probs) / len(probs) if probs else 0.0
            return {
                "is_speech": avg_prob > self.threshold,
                "confidence": avg_prob,
                "threshold": self.threshold
            }

    def detect_file(self, audio_path: str) -> Dict[str, Any]:
        """Detect speech in an audio file."""
        import wave
        with wave.open(audio_path, "rb") as f:
            channels = f.getnchannels()
            sample_width = f.getsampwidth()
            sample_rate = f.getframerate()
            frames = f.readframes(f.getnframes())

        audio = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)

        # Resample if needed (simple decimation)
        if sample_rate != self.sample_rate:
            ratio = sample_rate / self.sample_rate
            indices = np.arange(0, len(audio), ratio).astype(int)
            audio = audio[indices]

        return self.detect_speech(audio, return_timestamps=True)


def jarvis_detect_voice(audio_path: str, threshold: float = 0.5) -> Dict[str, Any]:
    """Tool wrapper for JARVIS tool registry."""
    engine = VADEngine(threshold=threshold)
    result = engine.detect_file(audio_path)
    return {
        "status": "success",
        **result
    }


if __name__ == "__main__":
    engine = VADEngine()
    print("VAD Engine initialized")
    # Test with silence
    silence = np.zeros(16000, dtype=np.float32)
    result = engine.detect_speech(silence)
    print(f"Silence test: is_speech={result['is_speech']}, confidence={result['confidence']:.4f}")
